import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from gundem import pipeline
from gundem.config import get_settings
from gundem.models import BanKeyword, Draft, Item, Source, Topic, WatchAccount, XCursor
from gundem.pipeline import (
    Deps,
    run_analysis_and_score_cycle,
    run_breaking_cycle,
    run_draft_cycle,
    run_gundem_cycle,
    run_rival_cycle,
    run_velocity_cycle,
    run_x_breaking_cycle,
)
from gundem.sources.x import XTweet


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost/gundem_test"
)


def utcnow() -> datetime:
    # RSS parsing drops entries dated in the real future, so feed tests run on the real clock.
    return datetime.now(timezone.utc).replace(microsecond=0)


def rss_xml(entries) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>https://news.test/{guid}</link><guid>{guid}</guid>"
        f"<description>{summary}</description><pubDate>{published.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>"
        for title, guid, summary, published in entries
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


class FakeX:
    def __init__(self, responses=None, default=([], None)):
        self.responses = list(responses or [])
        self.default = default
        self.calls = []

    def search_recent(self, job, query, since_id=None, max_results=10):
        self.calls.append({"job": job, "query": query, "since_id": since_id, "max_results": max_results})
        return self.responses.pop(0) if self.responses else self.default


class FakeWriter:
    def __init__(self):
        self.calls = []

    def _draft(self, topic, session, kind):
        self.calls.append((kind, topic.id))
        draft = Draft(topic_id=topic.id, kind=kind, x_variants=[{"angle": "haber", "text": "t"}], ig={})
        session.add(draft)
        session.flush()
        return draft

    def write_fast(self, topic, items, session):
        assert items, "writer must get the topic's items"
        return self._draft(topic, session, "fast")

    def write(self, topic, items, session, tone=None):
        assert items, "writer must get the topic's items"
        return self._draft(topic, session, "full")


class FakeAnalyzer:
    def __init__(self):
        self.calls = []

    def analyze(self, topic, items):
        self.calls.append(topic.id)
        topic.category = "diger"
        topic.fit_score = 8
        topic.x_queries = ["deneme"]
        return {"category": "diger", "fit": 8, "x_queries": ["deneme"], "sensitive": False}


class Notifier:
    def __init__(self):
        self.calls = []

    def __call__(self, topic, draft):
        self.calls.append((topic.id, draft.kind if draft else None))


@pytest.fixture
def factory(db):
    return sessionmaker(db.get_bind(), expire_on_commit=False)


@pytest.fixture
def listener():
    conn = psycopg.connect(TEST_DATABASE_URL.replace("+psycopg", ""), autocommit=True)
    conn.execute("LISTEN gundem")

    def drain() -> list[dict]:
        return [json.loads(n.payload) for n in conn.notifies(timeout=0.5)]

    yield drain
    conn.close()


@pytest.fixture
def http():
    with httpx.Client() as client:
        yield client


def make_deps(factory, http, **kw) -> Deps:
    fields = dict(x=None, analyzer=None, writer=None, notifier=None)
    fields.update(kw)
    return Deps(session_factory=factory, http=http, settings=get_settings(), **fields)


def add_source(db, name, url, tier=1, breaking_feed=False, org=None) -> Source:
    src = Source(name=name, org=org or name, kind="rss", url=url, tier=tier, lang="tr",
                 is_breaking_feed=breaking_feed)
    db.add(src)
    db.flush()
    return src


def add_topic(db, title, now, item_source=None, **kw) -> Topic:
    kw.setdefault("first_seen_at", now - timedelta(minutes=10))
    kw.setdefault("last_seen_at", now - timedelta(minutes=5))
    topic = Topic(title=title, lang="tr", **kw)
    db.add(topic)
    db.flush()
    if item_source is not None:
        db.add(Item(source_id=item_source.id, external_id=f"t{topic.id}", title=title, summary=title,
                    url=f"https://news.test/t{topic.id}", published_at=topic.first_seen_at,
                    first_seen_at=topic.first_seen_at, lang="tr", topic_id=topic.id))
        db.flush()
    return topic


@respx.mock
def test_breaking_cycle_fast_drafts_and_notifies(db, factory, http, listener):
    now = utcnow()
    aa = add_source(db, "AA Son Dakika", "https://feed.test/aa", tier=1, breaking_feed=True)
    gundem_src = add_source(db, "Gazete", "https://feed.test/gazete", tier=2)
    db.commit()
    feed = rss_xml([("Merkez Bankası faiz kararını açıkladı bugün", "aa-1",
                     "Merkez Bankası politika faizini sabit tuttu.", now - timedelta(minutes=5))])
    respx.get(aa.url).respond(200, content=feed)
    tier2 = respx.get(gundem_src.url).respond(200, content=rss_xml([]))

    writer, notifier = FakeWriter(), Notifier()
    deps = make_deps(factory, http, writer=writer, notifier=notifier)
    topics = run_breaking_cycle(deps, now)

    assert len(topics) == 1 and topics[0].is_breaking
    topic_id = topics[0].id
    assert not tier2.called, "breaking cycle reads only tier 1 sources"
    assert writer.calls == [("fast", topic_id)]
    assert notifier.calls == [(topic_id, "fast")]
    events = listener()
    assert {"type": "breaking", "topic_id": topic_id} in events
    with factory() as s:
        topic = s.get(Topic, topic_id)
        assert topic.score >= 90
        assert s.scalars(select(Draft.kind).where(Draft.topic_id == topic_id)).all() == ["fast"]

    # A second report of the same story joins the topic: no second draft and no second alert.
    respx.get(aa.url).respond(200, content=rss_xml([
        ("Merkez Bankası faiz kararını açıkladı bugün", "aa-1", "Sabit.", now - timedelta(minutes=5)),
        ("Merkez Bankası faiz kararını bugün açıkladı", "aa-2", "Faiz sabit.", now - timedelta(minutes=1)),
    ]))
    assert run_breaking_cycle(deps, now + timedelta(seconds=60)) == []
    with factory() as s:
        assert s.scalars(select(Item.topic_id).where(Item.external_id == "aa-2")).one() == topic_id
    assert writer.calls == [("fast", topic_id)]
    assert {"type": "breaking", "topic_id": topic_id} not in listener()


def test_x_breaking_tweet_becomes_item(db, factory, http):
    now = utcnow()
    db.add(WatchAccount(handle="ajansflas", role="breaking", tier=1))
    db.add(WatchAccount(handle="rakip", role="rival"))
    db.commit()
    text = "SON DAKİKA: İstanbul'da 4,2 büyüklüğünde deprem meydana geldi, AFAD açıklama yaptı. " * 3
    tweet = XTweet("1001", "ajansflas", text, now - timedelta(minutes=2), 5, 1, 0, 0)
    x = FakeX([([tweet], "1001"), ([], None)])
    writer = FakeWriter()
    deps = make_deps(factory, http, x=x, writer=writer)

    run_x_breaking_cycle(deps, now)

    call = x.calls[0]
    assert call["job"] == "breaking" and call["since_id"] is None
    assert "from:ajansflas" in call["query"] and "rakip" not in call["query"]
    with factory() as s:
        item = s.scalars(select(Item)).one()
        source = s.get(Source, item.source_id)
        assert item.external_id == "1001"
        assert item.title == text[:200]
        assert item.summary == text
        assert item.url == "https://x.com/ajansflas/status/1001"
        assert item.published_at == tweet.created_at
        assert item.is_breaking
        assert (source.kind, source.tier, source.org, source.url) == ("x", 1, "ajansflas", "x:ajansflas")
        assert not source.is_breaking_feed
        topic = s.get(Topic, item.topic_id)
        assert topic.is_breaking
        assert s.scalars(select(XCursor.since_id).where(XCursor.job == "breaking")).one() == "1001"
    assert writer.calls == [("fast", item.topic_id)]

    run_x_breaking_cycle(deps, now + timedelta(seconds=90))
    assert x.calls[1]["since_id"] == "1001"
    with factory() as s:
        assert len(s.scalars(select(Source).where(Source.kind == "x")).all()) == 1


def test_draft_cycle_breaking_first_and_capped(db, factory, http, listener):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    breaking = add_topic(db, "Son dakika konu", now, src, score=61, is_breaking=True)
    others = [add_topic(db, f"Konu {i}", now, src, score=90 - i) for i in range(6)]
    db.commit()
    assert get_settings().max_drafts_per_cycle == 5

    writer = FakeWriter()
    assert run_draft_cycle(make_deps(factory, http, writer=writer), now) == 5

    drafted = [topic_id for _, topic_id in writer.calls]
    assert drafted[0] == breaking.id
    assert drafted[1:] == [t.id for t in others[:4]]
    assert all(kind == "full" for kind, _ in writer.calls)
    events = listener()
    assert sum(1 for e in events if e["type"] == "draft") == 5

    # The next cycle picks up the remaining topics; drafted ones are not redone.
    writer.calls.clear()
    assert run_draft_cycle(make_deps(factory, http, writer=writer), now) == 2
    assert {t for _, t in writer.calls} == {others[4].id, others[5].id}


def test_banned_topic_gets_no_draft(db, factory, http):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    add_topic(db, "Yasaklı konu", now, src, score=95, is_breaking=True, banned=True)
    add_topic(db, "Hatalı konu", now, src, score=95, draft_error="Taslak üretilemedi")
    add_topic(db, "Düşük skor", now, src, score=10)
    db.commit()
    writer = FakeWriter()
    assert run_draft_cycle(make_deps(factory, http, writer=writer), now) == 0
    assert writer.calls == []


@respx.mock
def test_banned_breaking_item_gets_no_fast_draft(db, factory, http):
    now = utcnow()
    aa = add_source(db, "AA Son Dakika", "https://feed.test/aa", tier=1, breaking_feed=True)
    db.add(BanKeyword(phrase="maden faciası"))
    db.commit()
    feed = rss_xml([("Maden faciası soruşturmasında yeni gelişme yaşandı", "aa-9", "Ayrıntılar.",
                     now - timedelta(minutes=3))])
    respx.get(aa.url).respond(200, content=feed)
    writer = FakeWriter()
    topics = run_breaking_cycle(make_deps(factory, http, writer=writer), now)
    assert len(topics) == 1
    with factory() as s:
        assert s.get(Topic, topics[0].id).banned
    assert writer.calls == []


@respx.mock
def test_cycle_runs_without_keys(db, factory, http, listener):
    now = utcnow()
    aa = add_source(db, "AA Son Dakika", "https://feed.test/aa", tier=1, breaking_feed=True)
    gazete = add_source(db, "Gazete", "https://feed.test/gazete", tier=2)
    db.add(WatchAccount(handle="ajansflas", role="breaking", tier=1))
    db.commit()
    respx.get(aa.url).respond(200, content=rss_xml([
        ("Meclis yeni yasa teklifini kabul etti oylamada", "aa-1", "Teklif kabul edildi.", now - timedelta(minutes=4)),
    ]))
    respx.get(gazete.url).respond(200, content=rss_xml([
        ("Ünlü oyuncu yeni dizisini duyurdu sosyal medyada", "gz-1", "Dizi duyuruldu.", now - timedelta(minutes=20)),
    ]))
    deps = make_deps(factory, http)

    topics = run_breaking_cycle(deps, now)
    run_x_breaking_cycle(deps, now)
    assert run_gundem_cycle(deps, now) == 1
    run_rival_cycle(deps, now)
    run_velocity_cycle(deps, now)
    run_analysis_and_score_cycle(deps, now)
    assert run_draft_cycle(deps, now) == 0

    assert len(topics) == 1
    assert {"type": "breaking", "topic_id": topics[0].id} in listener()
    with factory() as s:
        assert len(s.scalars(select(Topic)).all()) == 2
        assert s.scalars(select(Draft)).all() == []


def test_build_deps_without_keys_leaves_fields_none():
    from gundem.worker import build_deps

    settings = get_settings().model_copy(update={
        "anthropic_api_key": None, "anthropic_model": None, "anthropic_fast_model": None, "x_bearer_token": None,
    })
    deps = build_deps(settings)
    try:
        assert (deps.x, deps.analyzer, deps.writer, deps.notifier) == (None, None, None, None)
    finally:
        deps.http.close()

    partial = settings.model_copy(update={"anthropic_api_key": "k", "anthropic_fast_model": "fast-model"})
    deps = build_deps(partial)
    try:
        assert deps.analyzer is not None and deps.writer is None, "full drafts need ANTHROPIC_MODEL"
    finally:
        deps.http.close()


def test_velocity_cycle_max_3_and_sets_prev(db, factory, http):
    now = utcnow()
    eligible = [
        add_topic(db, f"Yayılan konu {i}", now, score=90 - i, x_queries=["konu"], x_velocity=10.0 + i)
        for i in range(5)
    ]
    add_topic(db, "Son dakika", now, score=99, is_breaking=True, x_queries=["sd"])
    add_topic(db, "Yeni ölçülmüş", now, score=98, x_queries=["yeni"], x_velocity_measured_at=now - timedelta(minutes=10))
    add_topic(db, "Sorgusuz", now, score=97, x_queries=[])
    add_topic(db, "Düşük", now, score=20, x_queries=["dusuk"])
    old = add_topic(db, "Eski ölçüm", now, score=50 + 10, x_queries=["eski"],
                    x_velocity_measured_at=now - timedelta(minutes=45))
    db.commit()
    tweets = [XTweet("1", "a", "t", now - timedelta(minutes=10), 100, 50, 30, 20)]
    x = FakeX(default=(tweets, "1"))

    run_velocity_cycle(make_deps(factory, http, x=x), now)

    assert len(x.calls) == 3
    assert all(c["job"] == "velocity" and c["max_results"] == 10 for c in x.calls)
    with factory() as s:
        for i, topic in enumerate(eligible[:3]):
            fresh = s.get(Topic, topic.id)
            assert fresh.x_velocity_prev == 10.0 + i
            assert fresh.x_velocity == pytest.approx(20.0)
            assert fresh.x_velocity_measured_at == now
        for topic in eligible[3:] + [old]:
            assert s.get(Topic, topic.id).x_velocity_measured_at != now


@respx.mock
def test_retraction_flags_shared_topic(db, factory, http, listener):
    now = utcnow()
    gazete = add_source(db, "Gazete", "https://feed.test/gazete", tier=2)
    shared = add_topic(db, "Ünlü şarkıcı İzmir konserini iptal etti", now, gazete,
                       status="shared", shared_at=now - timedelta(minutes=3))
    fresh = add_topic(db, "Belediye otobüs ücretlerine zam yaptı", now, gazete)
    db.commit()
    respx.get(gazete.url).respond(200, content=rss_xml([
        ("Ünlü şarkıcı İzmir konserini iptal etti haberini yalanladı", "gz-2", "Şarkıcı haberi yalanladı.",
         now - timedelta(minutes=1)),
        ("Belediye otobüs ücretlerine zam yaptı iddiası düzeltme", "gz-3", "Düzeltme yapıldı.",
         now - timedelta(minutes=1)),
    ]))

    run_gundem_cycle(make_deps(factory, http), now)

    with factory() as s:
        assert s.get(Topic, shared.id).retracted
        assert not s.get(Topic, fresh.id).retracted, "only shared topics are flagged"
        assert s.scalars(select(Item.topic_id).where(Item.external_id == "gz-2")).one() == shared.id
    events = listener()
    assert {"type": "retraction", "topic_id": shared.id} in events
    assert {"type": "retraction", "topic_id": fresh.id} not in events


def test_rescore_decays_over_time(db, factory, http, listener):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    topic = add_topic(db, "Gençleri ilgilendiren yeni sınav kararı", now, src, fit_score=8,
                      category="egitim_sinav", first_seen_at=now - timedelta(minutes=5))
    db.commit()
    deps = make_deps(factory, http)

    run_analysis_and_score_cycle(deps, now)
    with factory() as s:
        first = s.get(Topic, topic.id).score
    assert first > 0
    assert {"type": "topic", "topic_id": topic.id} in listener()

    run_analysis_and_score_cycle(deps, now)
    assert {"type": "topic", "topic_id": topic.id} not in listener(), "unchanged score is not re-announced"

    run_analysis_and_score_cycle(deps, now + timedelta(hours=3))
    with factory() as s:
        later = s.get(Topic, topic.id).score
    assert later < first
    assert {"type": "topic", "topic_id": topic.id} in listener()


def test_rescore_applies_new_ban_keyword(db, factory, http):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    topic = add_topic(db, "Maden faciası soruşturmasında yeni gelişme", now, src, category="diger")
    db.commit()
    db.add(BanKeyword(phrase="maden faciası"))
    db.commit()
    run_analysis_and_score_cycle(make_deps(factory, http), now)
    with factory() as s:
        assert s.get(Topic, topic.id).banned


def test_analysis_caps_at_10_breaking_first(db, factory, http):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    plain = [add_topic(db, f"Analiz bekleyen konu {i}", now, src) for i in range(11)]
    breaking = add_topic(db, "Son dakika analiz", now, src, is_breaking=True,
                         first_seen_at=now - timedelta(hours=2))
    done = add_topic(db, "Analiz edilmiş", now, src, category="futbol", fit_score=3)
    db.commit()
    analyzer = FakeAnalyzer()

    run_analysis_and_score_cycle(make_deps(factory, http, analyzer=analyzer), now)

    assert len(analyzer.calls) == 10
    assert analyzer.calls[0] == breaking.id
    assert done.id not in analyzer.calls
    with factory() as s:
        assert s.get(Topic, breaking.id).category == "diger"
        assert sum(1 for t in plain if s.get(Topic, t.id).category is None) == 2


def test_rival_cycle_polls_and_matches(db, factory, http):
    now = utcnow()
    db.add(WatchAccount(handle="rakip", role="rival"))
    topic = add_topic(db, "Ünlü futbolcu yeni takımına transfer oldu", now)
    db.commit()
    tweet = XTweet("55", "rakip", "Ünlü futbolcu yeni takımına transfer oldu", now - timedelta(minutes=3), 10, 0, 0, 0)
    x = FakeX([([tweet], "55")])
    run_rival_cycle(make_deps(factory, http, x=x), now)
    assert x.calls[0]["job"] == "rival"
    with factory() as s:
        assert s.get(Topic, topic.id).rival_handle == "rakip"


def test_notify_sends_pg_notify_on_commit(factory, listener):
    with factory() as s:
        pipeline.notify(s, "topic", 7)
        assert listener() == []
        s.commit()
    assert listener() == [{"type": "topic", "topic_id": 7}]


def test_job_intervals():
    from gundem.worker import build_jobs

    jobs = {name: (fn, seconds) for name, fn, seconds in build_jobs()}
    assert {name: seconds for name, (_, seconds) in jobs.items()} == {
        "breaking_rss": 60, "x_breaking": 90, "gundem_rss": 300, "rivals": 900,
        "velocity": 600, "analysis_score": 60, "drafts": 30,
    }
    assert jobs["breaking_rss"][0] is run_breaking_cycle
    assert jobs["drafts"][0] is run_draft_cycle


def test_guarded_job_logs_and_swallows_errors(caplog):
    from gundem.worker import guarded

    def boom(deps, now):
        raise RuntimeError("kaboom")

    guarded("boom", boom, deps=object())()
    assert "boom" in caplog.text and "kaboom" in caplog.text


class FailingFastWriter(FakeWriter):
    def write_fast(self, topic, items, session):
        self.calls.append(("fast-failed", topic.id))
        topic.draft_error = "Taslak üretilemedi"
        return None


@respx.mock
def test_failed_fast_draft_does_not_block_full_draft(db, factory, http):
    now = utcnow()
    aa = add_source(db, "AA Son Dakika", "https://feed.test/aa", tier=1, breaking_feed=True)
    db.commit()
    respx.get(aa.url).respond(200, content=rss_xml([
        ("Büyük şehirde elektrik kesintisi saatlerdir sürüyor", "aa-5", "Kesinti sürüyor.",
         now - timedelta(minutes=2)),
    ]))
    writer = FailingFastWriter()
    deps = make_deps(factory, http, writer=writer)
    topics = run_breaking_cycle(deps, now)
    assert writer.calls == [("fast-failed", topics[0].id)]
    with factory() as s:
        assert s.get(Topic, topics[0].id).draft_error is None

    assert run_draft_cycle(deps, now) == 1
    assert writer.calls[-1] == ("full", topics[0].id)


class FlakyAnalyzer:
    def __init__(self, fail=True):
        self.fail = fail
        self.calls = []

    def analyze(self, topic, items):
        self.calls.append(topic.id)
        if self.fail:
            return None
        topic.category = "diger"
        return {"category": "diger", "fit": 5, "x_queries": [], "sensitive": False}


def test_failed_analysis_waits_15_minutes_then_retries(db, factory, http):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    topic = add_topic(db, "Analizi patlayan konu", now, src)
    db.commit()
    analyzer = FlakyAnalyzer()
    deps = make_deps(factory, http, analyzer=analyzer)

    run_analysis_and_score_cycle(deps, now)
    run_analysis_and_score_cycle(deps, now + timedelta(minutes=14))
    assert analyzer.calls == [topic.id], "not retried within 15 minutes"

    analyzer.fail = False
    run_analysis_and_score_cycle(deps, now + timedelta(minutes=15))
    assert analyzer.calls == [topic.id, topic.id]
    assert topic.id not in deps.analysis_failures
    with factory() as s:
        assert s.get(Topic, topic.id).category == "diger"


def test_analysis_skipped_after_3_failures(db, factory, http):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    topic = add_topic(db, "Hep patlayan konu", now, src, last_seen_at=now + timedelta(hours=2))
    db.commit()
    analyzer = FlakyAnalyzer()
    deps = make_deps(factory, http, analyzer=analyzer)
    for step in range(5):
        run_analysis_and_score_cycle(deps, now + timedelta(minutes=16 * step))
    assert analyzer.calls == [topic.id] * 3


def test_velocity_skips_dead_topics(db, factory, http):
    now = utcnow()
    live = add_topic(db, "Canlı konu", now, score=70, x_queries=["canli"])
    dead = [
        add_topic(db, "Paylaşılmış", now, score=99, x_queries=["a"], status="shared"),
        add_topic(db, "Atlanmış", now, score=99, x_queries=["a"], status="skipped"),
        add_topic(db, "Eski", now, score=99, x_queries=["a"], last_seen_at=now - timedelta(hours=7)),
        add_topic(db, "Yasaklı", now, score=99, x_queries=["a"], banned=True),
        add_topic(db, "Yalanlanmış", now, score=99, x_queries=["a"], retracted=True),
    ]
    db.commit()
    x = FakeX(default=([], None))
    run_velocity_cycle(make_deps(factory, http, x=x), now)
    assert len(x.calls) == 1
    with factory() as s:
        assert s.get(Topic, live.id).x_velocity_measured_at == now
        assert all(s.get(Topic, t.id).x_velocity_measured_at is None for t in dead)


def test_draft_cycle_rechecks_ban_before_writing(db, factory, http, listener):
    now = utcnow()
    src = add_source(db, "AA", "https://feed.test/aa")
    topic = add_topic(db, "Maden faciası soruşturmasında yeni gelişme", now, src, score=95)
    db.commit()
    db.add(BanKeyword(phrase="maden faciası"))
    db.commit()
    writer = FakeWriter()
    assert run_draft_cycle(make_deps(factory, http, writer=writer), now) == 0
    assert writer.calls == []
    with factory() as s:
        assert s.get(Topic, topic.id).banned
    assert {"type": "topic", "topic_id": topic.id} in listener()
