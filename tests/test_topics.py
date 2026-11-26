from datetime import datetime, timedelta, timezone

from gundem.models import Item, Source, Topic
from gundem.topics import (
    assign_topic, normalize_title, recompute_verification, similar, split_item,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
QUAKE = "İstanbul'da 4.2 büyüklüğünde deprem"
QUAKE_LONG = "SON DAKİKA İstanbul'da 4,2 büyüklüğünde deprem meydana geldi"

_n = 0


def make_source(db, org="AA", tier=1, kind="rss", lang="tr", name=None):
    s = Source(name=name or org, org=org, kind=kind, url="u", tier=tier, lang=lang)
    db.add(s)
    db.flush()
    return s


def make_item(db, source, title=QUAKE, summary=None, lang="tr", seen=NOW, is_breaking=False):
    global _n
    _n += 1
    it = Item(
        source_id=source.id, external_id=f"e{_n}", title=title, summary=summary,
        url="https://e.com", first_seen_at=seen, lang=lang, is_breaking=is_breaking,
    )
    db.add(it)
    db.flush()
    return it


def test_normalize_strips_prefix_and_turkish_case():
    assert normalize_title("SON DAKİKA: İstanbul'da Deprem!") == "istanbulda deprem"
    assert normalize_title("IŞIK haber") == "ışık haber"
    assert normalize_title("VİDEO: Foto: Flaş! Breaking - Kaza") == "kaza"


def test_similar_short_titles_score_zero():
    assert similar("deprem", "deprem") == 0
    assert similar("istanbul deprem", "istanbul deprem") == 0
    assert similar("a b c", "a b c") == 100


def test_similar_titles_merge(db):
    aa = make_source(db, "AA", 1)
    hu = make_source(db, "Hürriyet", 2)
    t1 = assign_topic(make_item(db, aa, QUAKE), db, NOW)
    t2 = assign_topic(make_item(db, hu, QUAKE_LONG), db, NOW)
    assert t1 is not None and t1.id == t2.id
    assert db.query(Topic).count() == 1


def test_single_word_trend_does_not_swallow_topics(db):
    aa = make_source(db, "AA", 1)
    tr = make_source(db, "Trends", 3, kind="trends")
    assign_topic(make_item(db, aa, QUAKE), db, NOW)
    it = make_item(db, tr, "deprem")
    assert assign_topic(it, db, NOW) is None
    assert it.topic_id is None
    assert db.query(Topic).count() == 1
    assert db.query(Topic).one().trends_hit is False


def test_trend_boosts_matching_topic(db):
    aa = make_source(db, "AA", 1)
    tr = make_source(db, "Trends", 3, kind="trends")
    t = assign_topic(make_item(db, aa, QUAKE), db, NOW)
    it = make_item(db, tr, QUAKE)
    assert assign_topic(it, db, NOW).id == t.id
    assert t.trends_hit is True
    assert it.topic_id == t.id


def test_tier3_non_trends_attaches_without_trends_hit(db):
    aa = make_source(db, "AA", 1)
    rd = make_source(db, "Reddit", 3)
    t = assign_topic(make_item(db, aa, QUAKE), db, NOW)
    assert assign_topic(make_item(db, rd, QUAKE), db, NOW).id == t.id
    assert t.trends_hit is False
    assert t.org_count == 1


def test_tier3_without_match_returns_none(db):
    rd = make_source(db, "Reddit", 3)
    assert assign_topic(make_item(db, rd, QUAKE), db, NOW) is None
    assert db.query(Topic).count() == 0


def test_two_orgs_verify(db):
    aa = make_source(db, "AA", 1)
    hu = make_source(db, "Hürriyet", 2)
    assign_topic(make_item(db, aa, QUAKE, "AFAD açıklaması: deprem 10 km derinlikte oldu."), db, NOW)
    t = assign_topic(make_item(db, hu, QUAKE_LONG, "Vatandaşlar sokağa döküldü, sarsıntı hissedildi."), db, NOW)
    assert t.verified is True
    assert t.org_count == 2


def test_same_org_two_feeds_not_verified(db):
    a1 = make_source(db, "AA", 1, name="AA son dakika")
    a2 = make_source(db, "AA", 1, name="AA genel")
    assign_topic(make_item(db, a1, QUAKE, "x1 farklı özet bir"), db, NOW)
    t = assign_topic(make_item(db, a2, QUAKE_LONG, "tamamen başka bir özet"), db, NOW)
    assert t.org_count == 1
    assert t.verified is False


def test_near_duplicate_summary_counts_as_one_org(db):
    aa = make_source(db, "AA", 1)
    hu = make_source(db, "Hürriyet", 2)
    summ = "İstanbul'da 4.2 büyüklüğünde deprem meydana geldi, AFAD açıklama yaptı."
    assign_topic(make_item(db, aa, QUAKE, summ), db, NOW)
    t = assign_topic(make_item(db, hu, QUAKE_LONG, summ), db, NOW)
    assert t.org_count == 1
    assert t.verified is False


def test_empty_summaries_never_collapse(db):
    aa = make_source(db, "AA", 1)
    hu = make_source(db, "Hürriyet", 2)
    assign_topic(make_item(db, aa, QUAKE, None), db, NOW)
    t = assign_topic(make_item(db, hu, QUAKE_LONG, ""), db, NOW)
    assert t.org_count == 2 and t.verified is True


def test_tier3_does_not_count_for_verification(db):
    aa = make_source(db, "AA", 1)
    rd = make_source(db, "Reddit", 3)
    assign_topic(make_item(db, aa, QUAKE, "bir"), db, NOW)
    t = assign_topic(make_item(db, rd, QUAKE, "iki"), db, NOW)
    assert t.org_count == 1 and t.verified is False


def test_editor_verified_stays_true(db):
    aa = make_source(db, "AA", 1)
    t = assign_topic(make_item(db, aa, QUAKE), db, NOW)
    t.verified_by = "ayse"
    recompute_verification(t, db)
    assert t.org_count == 1 and t.verified is True


def test_window_uses_last_seen(db):
    aa = make_source(db, "AA", 1)
    hu = make_source(db, "Hürriyet", 2)
    old = NOW - timedelta(hours=10)
    t = assign_topic(make_item(db, aa, QUAKE, seen=old), db, old)
    # last_seen refreshed 2h ago -> still inside 6h window although first_seen is 10h old
    t.last_seen_at = NOW - timedelta(hours=2)
    db.flush()
    assert assign_topic(make_item(db, hu, QUAKE_LONG), db, NOW).id == t.id
    # outside the window -> new topic
    t.last_seen_at = NOW - timedelta(hours=7)
    db.flush()
    other = make_source(db, "NTV", 2)
    t3 = assign_topic(make_item(db, other, QUAKE), db, NOW)
    assert t3.id != t.id


def test_different_language_not_merged(db):
    aa = make_source(db, "AA", 1)
    en = make_source(db, "Reuters", 1, lang="en")
    t1 = assign_topic(make_item(db, aa, QUAKE), db, NOW)
    t2 = assign_topic(make_item(db, en, QUAKE, lang="en"), db, NOW)
    assert t1.id != t2.id and t2.lang == "en"


def test_new_topic_takes_fields_from_item(db):
    aa = make_source(db, "AA", 1)
    seen = NOW - timedelta(minutes=3)
    t = assign_topic(make_item(db, aa, QUAKE, seen=seen, is_breaking=True), db, NOW)
    assert t.title == QUAKE and t.lang == "tr"
    assert t.first_seen_at == seen and t.last_seen_at == NOW
    assert t.is_breaking is True


def test_split_item_creates_new_topic(db):
    aa = make_source(db, "AA", 1)
    hu = make_source(db, "Hürriyet", 2)
    assign_topic(make_item(db, aa, QUAKE, "bir özet"), db, NOW)
    hi = make_item(db, hu, QUAKE_LONG, "başka özet")
    t = assign_topic(hi, db, NOW)
    assert t.verified is True
    new = split_item(hi.id, db, NOW)
    assert new.id != t.id and hi.topic_id == new.id
    assert new.title == QUAKE_LONG
    assert t.org_count == 1 and t.verified is False
    assert new.org_count == 1 and new.verified is False
