import json
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from sqlalchemy import select

from gundem.drafting import DraftWriter, build_messages, source_credit
from gundem.models import Draft, Item, Source, Topic
from gundem.voice import load_voice_examples

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)

HABER = "Belediye, merkezdeki yeni metro hattının yarın sabah 06.00'da yolcu almaya başlayacağını açıkladı; hat 12 durakla iki ilçeyi birbirine bağlayacak."
MERAK = "Yarın sabah işe giderken rotan değişebilir: merkezdeki yeni metro hattı 12 durakla açılıyor, ilk sefer saat 06.00'da kalkacak."
SORU = "Yeni metro hattı yarın 12 durakla açılıyor. Sen işe ya da okula giderken bu hattı kullanacak mısın, yoksa eski rotana mı devam?"


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=response)])


def full_json(haber=HABER, merak=MERAK, soru=SORU):
    return json.dumps({
        "x": [
            {"angle": "haber", "text": haber},
            {"angle": "merak", "text": merak},
            {"angle": "sana_etkisi_ya_da_soru", "text": soru},
        ],
        "instagram": {
            "cover": "Yeni metro hattı yarın açılıyor",
            "caption": "Merkezdeki yeni metro hattı yarın sabah ilk yolcularını alıyor.",
            "hashtags": ["#metro", "#ulaşım"],
            "slides": ["Yarın açılıyor", "12 durak", "İlk sefer 06.00"],
        },
    }, ensure_ascii=False)


def fast_json(text=HABER):
    return json.dumps({"x": [{"angle": "haber", "text": text}]}, ensure_ascii=False)


@pytest.fixture
def setup(db):
    aa = Source(name="AA Güncel", org="AA", kind="rss", url="https://aa.example/rss", tier=1, lang="tr")
    hurriyet = Source(name="Hürriyet Gündem", org="Hürriyet", kind="rss", url="https://h.example/rss", tier=2, lang="tr")
    db.add_all([aa, hurriyet])
    db.flush()
    topic = Topic(title="Yeni metro hattı yarın açılıyor", lang="tr", first_seen_at=NOW, last_seen_at=NOW, x_queries=[])
    db.add(topic)
    db.flush()
    items = [
        Item(source_id=hurriyet.id, external_id="h1", title="Yeni metro hattı yarın açılıyor",
             summary="Belediye merkezdeki 12 duraklı hattın yarın 06.00'da açılacağını duyurdu.",
             url="https://h.example/metro", first_seen_at=NOW, lang="tr", topic_id=topic.id),
        Item(source_id=aa.id, external_id="a1", title="Metro hattı 12 durakla hizmete giriyor",
             summary="Hat iki ilçeyi birbirine bağlayacak, ilk sefer 06.00'da.",
             url="https://aa.example/metro", first_seen_at=NOW, lang="tr", topic_id=topic.id),
    ]
    db.add_all(items)
    db.flush()
    return db, topic, items


def test_build_messages_has_sources_and_rules(setup):
    db, topic, items = setup
    system, messages = build_messages(topic, items, db)
    assert "ekleme yok" in system
    for phrase in ("ŞOK", "inanamayacaksınız", "dikkat çekti", "gündem oldu", "işte detaylar"):
        assert phrase in system
    assert "AFAD" in system and "İntihar haberinde yöntem" in system
    assert '"sana_etkisi_ya_da_soru"' in system
    user = messages[0]["content"]
    for item in items:
        assert item.url in user
        assert item.title in user
        assert item.summary in user
    assert "AA" in user and "Hürriyet" in user
    assert "hassas konu kurallarını uygula" not in user


def test_build_messages_sensitive_topic(setup):
    db, topic, items = setup
    topic.sensitive = True
    _, messages = build_messages(topic, items, db)
    assert "hassas konu kurallarını uygula" in messages[0]["content"]


def test_source_credit_uses_highest_tier(setup):
    _, _, items = setup
    assert source_credit(items) == "(AA)"
    assert source_credit(items[:1]) == "(Hürriyet)"


def test_write_creates_three_variants_with_credit(setup):
    db, topic, items = setup
    topic.draft_error = "eski hata"
    client = FakeClient(full_json())
    draft = DraftWriter(client, "main-model", "fast-model").write(topic, items, db)
    assert draft is not None and draft.id is not None
    assert draft.kind == "full" and draft.topic_id == topic.id
    assert [v["angle"] for v in draft.x_variants] == ["haber", "merak", "sana_etkisi_ya_da_soru"]
    for variant in draft.x_variants:
        assert variant["text"].endswith(" (AA)")
        assert len(variant["text"]) <= 280
        assert variant["truncated"] is False
        assert isinstance(variant["flags"], list)
    assert draft.ig["cover"] == "Yeni metro hattı yarın açılıyor"
    assert draft.ig["caption"].endswith("(AA)")
    assert len(draft.ig["slides"]) == 3
    assert 0.0 <= draft.similarity <= 1.0
    assert topic.draft_error is None
    call = client.calls[0]
    assert (call["model"], call["timeout"]) == ("main-model", 40)
    assert len(client.calls) == 1
    assert db.scalars(select(Draft)).all() == [draft]


def test_write_flags_unsupported_facts(setup):
    db, topic, items = setup
    client = FakeClient(full_json(haber="Belediye yeni metro hattını 15 durakla açıyor, Mehmet Bey de törende olacak."))
    draft = DraftWriter(client, "m", "f").write(topic, items, db)
    assert draft.x_variants[0]["flags"] == ["15", "Mehmet", "Bey"]
    assert draft.x_variants[1]["flags"] == []


def test_long_variant_retried_then_truncated(setup):
    db, topic, items = setup
    long_text = ("Belediye merkezdeki yeni metro hattını yarın açıyor ve " * 6).strip()
    client = FakeClient(full_json(merak=long_text), full_json(merak=long_text + " uzun"))
    draft = DraftWriter(client, "m", "f").write(topic, items, db)
    assert len(client.calls) == 2
    retry = client.calls[1]["messages"]
    assert retry[1]["role"] == "assistant"
    assert "kısalt" in retry[2]["content"].lower()
    merak = draft.x_variants[1]
    assert merak["truncated"] is True
    assert merak["text"].endswith("… (AA)")
    assert len(merak["text"]) <= 280
    body = merak["text"][: -len("… (AA)")]
    assert long_text.startswith(body) and long_text[len(body)] == " "
    assert draft.x_variants[0]["truncated"] is False


def test_long_variant_fixed_by_retry(setup):
    db, topic, items = setup
    client = FakeClient(full_json(merak="çok " * 80), full_json())
    draft = DraftWriter(client, "m", "f").write(topic, items, db)
    assert len(client.calls) == 2
    assert all(v["truncated"] is False for v in draft.x_variants)
    assert draft.x_variants[1]["text"] == MERAK + " (AA)"


def test_invalid_json_retried_once(setup):
    db, topic, items = setup
    client = FakeClient("bu json değil", full_json())
    draft = DraftWriter(client, "m", "f").write(topic, items, db)
    assert draft is not None
    assert len(client.calls) == 2
    assert client.calls[1]["messages"][1] == {"role": "assistant", "content": "bu json değil"}


def test_invalid_json_twice_sets_draft_error(setup):
    db, topic, items = setup
    client = FakeClient("bozuk", '{"x": []}')
    assert DraftWriter(client, "m", "f").write(topic, items, db) is None
    assert len(client.calls) == 2
    assert topic.draft_error
    assert db.scalars(select(Draft)).all() == []


def test_never_more_than_three_calls(setup):
    db, topic, items = setup
    long_text = "uzun " * 70
    client = FakeClient("bozuk", full_json(haber=long_text), full_json(haber=long_text), full_json())
    draft = DraftWriter(client, "m", "f").write(topic, items, db)
    assert len(client.calls) == 3
    assert draft.x_variants[0]["truncated"] is True


@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.example")),
    anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example")),
    anthropic.InternalServerError(
        "boom",
        response=httpx.Response(500, request=httpx.Request("POST", "https://api.example")),
        body=None,
    ),
])
def test_api_error_sets_draft_error(setup, error):
    db, topic, items = setup
    client = FakeClient(error)
    assert DraftWriter(client, "m", "f").write(topic, items, db) is None
    assert topic.draft_error == "Taslak üretilemedi"
    assert db.scalars(select(Draft)).all() == []


def test_fast_draft_single_variant(setup):
    db, topic, items = setup
    client = FakeClient(fast_json())
    draft = DraftWriter(client, "main-model", "fast-model").write_fast(topic, items, db)
    assert draft.kind == "fast"
    assert len(draft.x_variants) == 1
    assert draft.x_variants[0]["angle"] == "haber"
    assert draft.x_variants[0]["text"] == HABER + " (AA)"
    assert draft.ig == {}
    call = client.calls[0]
    assert (call["model"], call["timeout"]) == ("fast-model", 15)
    assert "ekleme yok" in call["system"]


def test_fast_draft_api_error(setup):
    db, topic, items = setup
    client = FakeClient(anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.example")))
    assert DraftWriter(client, "m", "f").write_fast(topic, items, db) is None
    assert topic.draft_error == "Taslak üretilemedi"


def test_tone_passed_to_prompt(setup):
    db, topic, items = setup
    client = FakeClient(full_json())
    DraftWriter(client, "m", "f").write(topic, items, db, tone="esprili")
    assert "daha esprili" in client.calls[0]["messages"][0]["content"]


def test_unknown_tone_rejected(setup):
    db, topic, items = setup
    with pytest.raises(ValueError):
        DraftWriter(FakeClient(), "m", "f").write(topic, items, db, tone="agresif")


def test_voice_examples_in_system_prompt(setup):
    db, topic, items = setup
    examples = load_voice_examples()
    assert 6 <= len(examples) <= 10
    for example in examples:
        assert {"platform", "angle", "text", "approved_by"} <= example.keys()
    system, _ = build_messages(topic, items, db)
    assert "ilham, kopyalama" in system
    shown = [e for e in examples if e["text"] in system]
    assert 3 <= len(shown) <= 6


def test_draft_similarity_high_when_variant_copies_source(setup):
    db, topic, items = setup
    client = FakeClient(full_json(merak=items[0].summary))
    draft = DraftWriter(client, "m", "f").write(topic, items, db)
    assert draft.similarity > 0.9
