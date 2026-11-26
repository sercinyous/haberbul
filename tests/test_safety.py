from datetime import datetime, timezone

from gundem.models import BanKeyword, Item, Topic
from gundem.safety import apply_safety, detect_retraction

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def topic(db, title="Normal konu") -> Topic:
    t = Topic(title=title, lang="tr", first_seen_at=NOW, last_seen_at=NOW)
    db.add(t)
    db.flush()
    return t


def item(title="Başlık", summary=None) -> Item:
    return Item(source_id=1, external_id="x", title=title, summary=summary, url="u",
                first_seen_at=NOW, lang="tr")


def test_ban_keyword_marks_banned(db):
    db.add(BanKeyword(phrase="Gizli Soruşturma"))
    t = topic(db)
    apply_safety(t, [item("Haber", "gizli soruşturma hakkında detaylar")], db)
    assert t.banned is True


def test_ban_keyword_added_later_applies(db):
    t = topic(db)
    items = [item("Bir olay oldu")]
    apply_safety(t, items, db)
    assert t.banned is False
    db.add(BanKeyword(phrase="olay"))
    apply_safety(t, items, db)
    assert t.banned is True


def test_inactive_ban_ignored(db):
    db.add(BanKeyword(phrase="yasaklı", active=False))
    t = topic(db)
    apply_safety(t, [item("Yasaklı kelime geçiyor")], db)
    assert t.banned is False


def test_ban_matches_whole_words_only(db):
    db.add(BanKeyword(phrase="kar"))
    t = topic(db)
    apply_safety(t, [item("Karabük'te maç")], db)
    assert t.banned is False


def test_sensitive_keyword_marks_sensitive(db):
    t = topic(db)
    apply_safety(t, [item("İstanbul'da patlama meydana geldi")], db)
    assert t.sensitive is True
    assert t.banned is False


def test_sensitive_in_summary_and_never_cleared(db):
    t = topic(db)
    apply_safety(t, [item("Haber", "Cenaze töreni yapıldı")], db)
    assert t.sensitive is True
    apply_safety(t, [item("Masum haber")], db)
    assert t.sensitive is True


def test_clean_topic_unflagged(db):
    t = topic(db)
    apply_safety(t, [item("Dolar rekor kırdı", "Piyasalar hareketli")], db)
    assert (t.banned, t.sensitive) == (False, False)


def test_detect_retraction():
    assert detect_retraction(item("AA iddiayı YALANLADI"))
    assert detect_retraction(item("Haber", "Düzeltme: önceki metinde hata"))
    assert detect_retraction(item("Correction: figure was wrong"))
    assert detect_retraction(item("Story retracted by outlet"))
    assert detect_retraction(item("Asılsız çıktı"))
    assert not detect_retraction(item("Normal haber", "Her şey yolunda"))
