import json
import logging
import re

import anthropic
from sqlalchemy.orm import Session, object_session

from gundem.factcheck import factcheck, max_sentence_similarity, style_flags
from gundem.models import Draft, Item, Source, Topic
from gundem.voice import load_voice_examples

log = logging.getLogger(__name__)

X_LIMIT = 280
ANGLES = ("haber", "merak", "sana_etkisi_ya_da_soru")
TONES = {
    "kisa": "Ton: her metni daha kısa ve öz yaz.",
    "esprili": "Ton: daha esprili yaz; hassas konuda ve can kaybında espri yapma.",
    "ciddi": "Ton: daha ciddi ve sade yaz.",
}
MAX_ITEMS = 8
MAX_EXAMPLES = 6
MAX_HASHTAGS = 8
MAX_SLIDES = 5
API_ERROR = "Taslak üretilemedi"
FORMAT_ERROR = "Taslak biçimi bozuk geldi"

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TRIM = " ,;:.-–—"

_RULES = """Sen Pusholder'ın sosyal medya editörüsün. Pusholder, 18-30 yaş arası Türk gençliğine yönelik bir dijital medya markasıdır. Sana bir haber konusunun kaynakları verilecek; bunlardan paylaşım taslağı yazacaksın. Taslağı bir editör kontrol edip kendisi paylaşır.

DİL VE ÜSLUP
- Pusholder dili: genç, hızlı, merak uyandıran ama abartmayan. Sade, akıcı Türkçe; kısa cümleler.
- Aşağıdaki örnekler yalnızca ilham içindir, kopya değildir: cümlelerini ve kalıplarını tekrar etme.

DOĞRULUK
- Yalnızca verilen kaynak bilgisini kullan, ekleme yok: kaynakta olmayan isim, sayı, tarih, yer, alıntı, sebep veya yorum ekleme; tahmin yürütme.
- Kaynak cümlesini kopyalama; bilgiyi kendi cümlelerinle yeniden yaz.
- Yabancı dildeki (global) haberi Türkçeye çevir. Türkiye'deki okur için neden önemli olduğunu yalnızca kaynakta buna dayanak varsa söyle; dayanak yoksa böyle bir bağlantı kurma.
- Kesinleşmemiş bilgiyi kesinmiş gibi yazma; "bildirildi", "iddia edildi" gibi atıf kalıpları kullan. Bilgiyi kimin açıkladığı kaynakta belliyse belirt.

BİÇİM
- Her metnin ilk cümlesi, akışta tek başına okunduğunda da çalışmalı: ne oldu, kim, nerede.
- X metni 120-200 karakter hedef. Kaynak künyesi ve link yazma; künye sistem tarafından eklenir.

YASAK KALIPLAR (asla kullanma)
- "ŞOK"
- "inanamayacaksınız"
- "dikkat çekti"
- "gündem oldu"
- "işte detaylar"
- tamamı büyük harfle yazılmış başlık veya cümle
- art arda emoji (yan yana iki veya daha fazla emoji)
- can kaybı içeren haberde emoji veya ünlem işareti

HASSAS KONU KURALLARI (her haberde geçerli; kullanıcı "hassas konu kurallarını uygula" derse özellikle dikkat et)
- Mağdurun, çocuğun veya şüphelinin kimliğini kaynağın verdiğinin ötesinde verme.
- Terör örgütlerinin açıklamalarını alıntılama.
- Can kaybını yalnızca resmi kaynağa atıfla ver (ör. AFAD, valilik).
- İntihar haberinde yöntem belirtme.
- Kişi veya örgüt adı içeren hashtag kullanma.
- Hassas konuda ve can kaybında espri yapma."""

_FULL_TASK = """GÖREV
X için üç ayrı açıdan birer metin ve bir Instagram taslağı yaz.
- haber: ne olduğunu düz ve net anlatan metin.
- merak: merak uyandıran ama abartmayan, tık tuzağı olmayan metin.
- sana_etkisi_ya_da_soru: okura etkisini (kaynakta dayanağı varsa) anlatan ya da okuru düşündüren bir soru soran metin.
- Instagram: cover 4-7 kelimelik kapak metni; caption açıklama metni; hashtags en fazla 8; slides 3-5 kısa slayt metni.

ÇIKTI
Yalnızca JSON döndür; başka metin, açıklama veya kod bloğu yazma. Şema:
{"x":[{"angle":"haber","text":"..."},{"angle":"merak","text":"..."},{"angle":"sana_etkisi_ya_da_soru","text":"..."}],"instagram":{"cover":"4-7 kelime","caption":"...","hashtags":["#..."],"slides":["...","...","..."]}}"""

_FAST_TASK = """GÖREV
Son dakika haberi: X için tek bir düz haber metni yaz (angle: haber). Ne olduğunu net anlat.

ÇIKTI
Yalnızca JSON döndür; başka metin, açıklama veya kod bloğu yazma. Şema:
{"x":[{"angle":"haber","text":"..."}]}"""


class DraftFormatError(ValueError):
    pass


def source_credit(items: list[Item]) -> str:
    """"(ORG)" of the highest-tier (lowest tier number) source; items must be attached to a session."""
    best: Source | None = None
    for item in items:
        session = object_session(item)
        if session is None:
            raise ValueError("source_credit needs items attached to a session")
        source = session.get(Source, item.source_id)
        if source is not None and (best is None or source.tier < best.tier):
            best = source
    return f"({best.org})" if best else ""


def build_messages(
    topic: Topic, items: list[Item], session: Session, tone: str | None = None,
    fast: bool = False, examples: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    if tone is not None and tone not in TONES:
        raise ValueError(f"unsupported tone: {tone}")
    if examples is None:
        examples = load_voice_examples()
    block = _examples_block(examples, fast)
    system = "\n\n".join([_RULES, _FAST_TASK if fast else _FULL_TASK, block]).strip()

    lines = [f"Konu: {topic.title}"]
    if topic.category:
        lines.append(f"Kategori: {topic.category}")
    if topic.sensitive:
        lines.append("Hassas konu: evet, hassas konu kurallarını uygula.")
    else:
        lines.append("Hassas konu: hayır")
    if tone:
        lines.append(TONES[tone])
    lines.append("")
    lines.append("Kaynaklar:")
    for number, (item, source) in enumerate(_ranked(items, session), start=1):
        lines.append(f"{number}.")
        lines.append(f"kuruluş: {source.org if source else '-'}")
        lines.append(f"başlık: {item.title}")
        lines.append(f"özet: {item.summary or '-'}")
        lines.append(f"url: {item.url}")
    return system, [{"role": "user", "content": "\n".join(lines)}]


class DraftWriter:
    def __init__(self, client, model: str, fast_model: str):
        self.client = client
        self.model = model
        self.fast_model = fast_model
        self.examples = load_voice_examples()

    def write_fast(self, topic: Topic, items: list[Item], session: Session) -> Draft | None:
        """Breaking-news draft: one plain X text, fast model, no Instagram."""
        return self._write(topic, items, session, fast=True, tone=None)

    def write(self, topic: Topic, items: list[Item], session: Session, tone: str | None = None) -> Draft | None:
        """Full draft: three X angles plus Instagram. Draft is added and flushed; caller commits."""
        return self._write(topic, items, session, fast=False, tone=tone)

    def _write(self, topic, items, session, fast: bool, tone: str | None) -> Draft | None:
        system, messages = build_messages(topic, items, session, tone=tone, fast=fast, examples=self.examples)
        model, timeout, max_tokens = (self.fast_model, 15, 500) if fast else (self.model, 40, 1500)

        # Breaking drafts must arrive in seconds: no hidden SDK retries on top of our own.
        client = self.client.with_options(max_retries=0) if fast else self.client

        def call(conversation: list[dict]) -> str:
            response = client.messages.create(
                model=model, max_tokens=max_tokens, timeout=timeout, system=system, messages=conversation,
            )
            return _first_text(response)

        credit = source_credit(items)
        try:
            # At most 2 calls for JSON problems and 2 for length problems: never more than 3 in total.
            # Fast drafts skip the length retry and are truncated right away.
            raw = call(messages)
            try:
                result = _parse(raw, fast)
            except DraftFormatError as exc:
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Yanıtın kullanılamadı ({exc}). Yalnızca şemaya uygun geçerli JSON döndür."},
                ]
                raw = call(messages)
                result = _parse(raw, fast)
            too_long = [v for v in result["x"] if len(_with_credit(v["text"], credit)) > X_LIMIT]
            if too_long and not fast:
                angles = ", ".join(v["angle"] for v in too_long)
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": (
                        f"Şu metinler künye dahil {X_LIMIT} karakteri aşıyor: {angles}. "
                        "Bunları kısalt (120-200 karakter hedef); aynı şemayla tüm JSON'u yeniden döndür."
                    )},
                ]
                try:
                    result = _parse(call(messages), fast)
                except (DraftFormatError, anthropic.APIError):
                    # We already hold a usable draft; fall back to truncating it.
                    log.warning("shortened draft for topic %s unusable; truncating", topic.id, exc_info=True)
        except anthropic.APIError:
            log.warning("draft API call failed for topic %s", topic.id, exc_info=True)
            topic.draft_error = API_ERROR
            return None
        except DraftFormatError:
            log.warning("draft response unusable for topic %s", topic.id, exc_info=True)
            topic.draft_error = FORMAT_ERROR
            return None

        source_text = "\n".join(f"{i.title}\n{i.summary or ''}" for i in items)
        variants = []
        for v in result["x"]:
            text, truncated = _fit(v["text"], credit)
            variants.append({
                "angle": v["angle"], "text": text, "truncated": truncated,
                "flags": factcheck(text, source_text, credit=credit) + style_flags(v["text"]),
            })
        # Bodies are checked without the code-added credit.
        checked = [v["text"] for v in result["x"]]
        ig = {}
        if not fast:
            ig = dict(result["instagram"])
            checked += [ig["caption"], *ig["slides"]]
            ig_flags: list[str] = []
            for part in [ig["cover"], ig["caption"], *ig["slides"]]:
                ig_flags += [f for f in style_flags(part) if f not in ig_flags]
            ig["flags"] = ig_flags
            ig["caption"] = _with_credit(ig["caption"], credit)
        similarity = max_sentence_similarity(checked, source_text)

        draft = Draft(
            topic_id=topic.id, kind="fast" if fast else "full", x_variants=variants, ig=ig, similarity=similarity,
        )
        topic.draft_error = None
        session.add(draft)
        session.flush()
        return draft


def _ranked(items: list[Item], session: Session) -> list[tuple[Item, Source | None]]:
    pairs = [(item, session.get(Source, item.source_id)) for item in items]
    pairs.sort(key=lambda pair: pair[1].tier if pair[1] else 99)
    return pairs[:MAX_ITEMS]


def _examples_block(examples: list[dict], fast: bool) -> str:
    x = [e for e in examples if e.get("platform") == "x"]
    instagram = [e for e in examples if e.get("platform") == "instagram"]
    chosen = x[:MAX_EXAMPLES] if fast else x[: MAX_EXAMPLES - 2] + instagram[:2]
    if not chosen:
        return ""
    lines = ["ÖRNEKLER (ilham, kopyalama)"]
    lines += [f"- [{e.get('platform', '?')} / {e.get('angle', '?')}] {e['text']}" for e in chosen]
    return "\n".join(lines)


def _first_text(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise DraftFormatError("yanıtta metin yok")


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DraftFormatError(f"{what} boş veya metin değil")
    return value.strip()


def _parse(raw: str, fast: bool) -> dict:
    try:
        data = json.loads(_FENCE.sub("", raw.strip()))
    except json.JSONDecodeError as exc:
        raise DraftFormatError("geçersiz JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("x"), list):
        raise DraftFormatError("'x' listesi yok")
    texts = {}
    for entry in data["x"]:
        if isinstance(entry, dict) and entry.get("angle") in ANGLES:
            texts[entry["angle"]] = _text(entry.get("text"), f"x.{entry['angle']}")
    wanted = ("haber",) if fast else ANGLES
    missing = [angle for angle in wanted if angle not in texts]
    if missing:
        raise DraftFormatError(f"eksik açı: {', '.join(missing)}")
    result = {"x": [{"angle": angle, "text": texts[angle]} for angle in wanted]}
    if fast:
        return result

    ig = data.get("instagram")
    if not isinstance(ig, dict):
        raise DraftFormatError("'instagram' yok")
    hashtags = ig.get("hashtags") or []
    slides = ig.get("slides")
    if not isinstance(hashtags, list) or not isinstance(slides, list):
        raise DraftFormatError("hashtags/slides liste değil")
    slides = [s.strip() for s in slides if isinstance(s, str) and s.strip()][:MAX_SLIDES]
    if not slides:
        raise DraftFormatError("slides boş")
    result["instagram"] = {
        "cover": _text(ig.get("cover"), "instagram.cover"),
        "caption": _text(ig.get("caption"), "instagram.caption"),
        "hashtags": [h.strip() for h in hashtags if isinstance(h, str) and h.strip()][:MAX_HASHTAGS],
        "slides": slides,
    }
    return result


def _with_credit(text: str, credit: str) -> str:
    return f"{text} {credit}" if credit else text


def _fit(body: str, credit: str) -> tuple[str, bool]:
    """Append the credit; if over the X limit, cut the body at a word boundary and add "…"."""
    full = _with_credit(body, credit)
    if len(full) <= X_LIMIT:
        return full, False
    budget = X_LIMIT - len(_with_credit("…", credit))
    cut = body[:budget]
    if " " in cut and body[budget] != " ":
        cut = cut.rsplit(" ", 1)[0]
    return _with_credit(cut.rstrip(_TRIM) + "…", credit), True
