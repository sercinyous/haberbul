import json
import logging
import re

from gundem.models import Item, Topic

log = logging.getLogger(__name__)

CATEGORIES = (
    "unlu", "futbol", "para_fiyat", "egitim_sinav", "absurt_komik", "ofke_tartisma",
    "teknoloji_oyun", "nostalji", "siyaset", "dunya", "diger",
)
MAX_ITEMS = 8

SYSTEM_PROMPT = (
    "Pusholder, 18-30 yaş arası Türk gençliğine yönelik bir dijital medya markasıdır. "
    "Sana bir haber konusu verilecek. Yalnızca JSON döndür, başka metin yazma: "
    '{"category": ' + " | ".join(f'"{c}"' for c in CATEGORIES) + ", "
    '"fit": 0-10 tam sayı (konunun bu kitleye uygunluğu), '
    '"x_queries": [X aramasında kullanılacak 2-3 kısa anahtar kelime veya hashtag öbeği], '
    '"sensitive": true/false (ölüm, şiddet, istismar, çocuk, intihar gibi hassas konu mu)}.'
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class TopicAnalyzer:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def analyze(self, topic: Topic, items: list[Item]) -> dict | None:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                timeout=15,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _prompt(topic, items)}],
            )
            data = json.loads(_FENCE.sub("", _first_text(response).strip()))
            result = _clean(data)
        except Exception:
            log.warning("topic analysis failed for topic %s", topic.id, exc_info=True)
            return None
        topic.category = result["category"]
        topic.fit_score = result["fit"]
        topic.x_queries = result["x_queries"]
        if result["sensitive"]:
            topic.sensitive = True
        return result


def _prompt(topic: Topic, items: list[Item]) -> str:
    lines = [f"Konu: {topic.title}"]
    for item in items[:MAX_ITEMS]:
        lines.append(f"- {item.title}" + (f" | {item.summary[:200]}" if item.summary else ""))
    return "\n".join(lines)


def _first_text(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("no text block in response")


def _clean(data: dict) -> dict:
    category = data.get("category")
    if category not in CATEGORIES:
        category = "diger"
    fit = max(0, min(10, int(data.get("fit", 0))))
    queries = [q.strip() for q in data.get("x_queries", []) if isinstance(q, str) and q.strip()][:3]
    return {"category": category, "fit": fit, "x_queries": queries, "sensitive": data.get("sensitive") is True}
