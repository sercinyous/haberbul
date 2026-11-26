from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from gundem.models import RivalPost, Topic, WatchAccount, XCursor
from gundem.rivals import match_rival_coverage, poll_rivals, rival_stats
from gundem.sources.x import XTweet

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


class FakeX:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search_recent(self, job, query, since_id=None, max_results=10):
        self.calls.append({"job": job, "query": query, "since_id": since_id, "max_results": max_results})
        return self.responses.pop(0)


def tweet(id, handle, text="Deprem sonrası bakan açıklama yaptı", minutes_ago=10, likes=0, reposts=0):
    return XTweet(id, handle, text, NOW - timedelta(minutes=minutes_ago), likes, reposts, 0, 0)


def watch(db, handle, role="rival", enabled=True):
    db.add(WatchAccount(handle=handle, role=role, enabled=enabled))
    db.flush()


def test_poll_uses_single_or_query(db):
    watch(db, "rakip1")
    watch(db, "rakip2")
    watch(db, "pusholder", "own")
    watch(db, "breakingacc", "breaking")
    watch(db, "kapali", "rival", enabled=False)
    x = FakeX([([tweet("1", "rakip1")], "1")])
    assert poll_rivals(x, db, NOW) == 1
    assert len(x.calls) == 1
    call = x.calls[0]
    assert call["job"] == "rival" and call["max_results"] == 10 and call["since_id"] is None
    for h in ("rakip1", "rakip2", "pusholder"):
        assert f"from:{h}" in call["query"]
    assert "breakingacc" not in call["query"] and "kapali" not in call["query"]
    assert db.scalars(select(RivalPost)).one().handle == "rakip1"


def test_poll_without_accounts_or_client_skips_x(db):
    x = FakeX([])
    assert poll_rivals(x, db, NOW) == 0
    assert x.calls == []
    watch(db, "rakip1")
    assert poll_rivals(None, db, NOW) == 0


def test_poll_updates_cursor(db):
    watch(db, "rakip1")
    x = FakeX([([tweet("5", "rakip1"), tweet("6", "rakip1")], "6"), ([tweet("7", "rakip1")], "7")])
    assert poll_rivals(x, db, NOW) == 2
    assert db.scalars(select(XCursor).where(XCursor.job == "rival")).one().since_id == "6"
    assert poll_rivals(x, db, NOW) == 1
    assert x.calls[1]["since_id"] == "6"
    assert db.scalars(select(XCursor).where(XCursor.job == "rival")).one().since_id == "7"
    # duplicate tweet is not inserted twice
    x2 = FakeX([([tweet("7", "rakip1")], "7")])
    assert poll_rivals(x2, db, NOW) == 0


def test_poll_multiple_queries_share_since_id_and_keep_max(db, monkeypatch):
    watch(db, "a")
    watch(db, "b")
    db.add(XCursor(job="rival", since_id="3"))
    db.flush()
    monkeypatch.setattr("gundem.rivals.build_queries", lambda from_handles: ["q1", "q2"])
    x = FakeX([([tweet("10", "a")], "10"), ([tweet("12", "b")], "12")])
    assert poll_rivals(x, db, NOW) == 2
    assert [c["since_id"] for c in x.calls] == ["3", "3"]
    assert db.scalars(select(XCursor)).one().since_id == "12"


def make_topic(db, title="Deprem sonrası bakan açıklama yaptı", hours_ago=1):
    t = Topic(title=title, lang="tr", first_seen_at=NOW - timedelta(hours=hours_ago),
              last_seen_at=NOW - timedelta(hours=hours_ago), x_queries=[])
    db.add(t)
    db.flush()
    return t


def post(db, tweet_id, handle, text, minutes_ago, likes=0, reposts=0, topic_id=None):
    p = RivalPost(handle=handle, tweet_id=tweet_id, text=text,
                  created_at_x=NOW - timedelta(minutes=minutes_ago),
                  like_count=likes, repost_count=reposts, reply_count=0, quote_count=0,
                  topic_id=topic_id)
    db.add(p)
    db.flush()
    return p


def test_rival_tweet_marks_topic(db):
    watch(db, "rakip1")
    watch(db, "pusholder", "own")
    t = make_topic(db)
    other = make_topic(db, "Tamamen alakasız futbol maçı sonucu")
    rp = post(db, "1", "rakip1", "Deprem sonrası bakan açıklama yaptı!", 30, likes=60)
    own = post(db, "2", "pusholder", "Deprem sonrası bakan açıklama yaptı", 5)
    unrelated = post(db, "3", "rakip1", "Bambaşka konuda uzun bir tweet metni", 5)
    assert match_rival_coverage(db, NOW) == 3 - 1
    assert rp.topic_id == t.id and own.topic_id == t.id and unrelated.topic_id is None
    assert t.rival_handle == "rakip1" and t.rival_text == rp.text
    assert t.rival_at == rp.created_at_x
    assert t.rival_eng_per_min == 60 / 30
    assert other.rival_handle is None


def test_own_tweet_does_not_set_rival_fields(db):
    watch(db, "pusholder", "own")
    t = make_topic(db)
    own = post(db, "2", "pusholder", "Deprem sonrası bakan açıklama yaptı", 5, likes=50)
    assert match_rival_coverage(db, NOW) == 1
    assert own.topic_id == t.id
    assert t.rival_handle is None and t.rival_at is None and t.rival_text is None
    assert t.rival_eng_per_min == 0


def test_first_rival_wins(db):
    watch(db, "gec")
    watch(db, "erken")
    t = make_topic(db)
    post(db, "1", "gec", "Deprem sonrası bakan açıklama yaptı", 5)
    post(db, "2", "erken", "Deprem sonrası bakan açıklama yaptı", 20)
    match_rival_coverage(db, NOW)
    assert t.rival_handle == "erken"
    # a later rival tweet in a later run does not override
    post(db, "3", "gec", "Deprem sonrası bakan açıklama yaptı", 2)
    match_rival_coverage(db, NOW)
    assert t.rival_handle == "erken"


def test_old_topics_and_posts_ignored(db):
    watch(db, "rakip1")
    old_topic = make_topic(db, hours_ago=8)
    p = post(db, "1", "rakip1", "Deprem sonrası bakan açıklama yaptı", 5)
    assert match_rival_coverage(db, NOW) == 0
    assert p.topic_id is None and old_topic.rival_handle is None
    make_topic(db)
    old_post = post(db, "2", "rakip1", "Deprem sonrası bakan açıklama yaptı", 7 * 60)
    match_rival_coverage(db, NOW)
    assert old_post.topic_id is None


def test_rival_stats_avg(db):
    watch(db, "rakip1")
    watch(db, "pusholder", "own")
    post(db, "1", "rakip1", "düşük", 30, likes=60, reposts=40)
    post(db, "2", "rakip1", "yüksek", 20, likes=200, reposts=100)
    post(db, "3", "pusholder", "kendi", 10, likes=5)
    post(db, "4", "rakip1", "çok eski", 600, likes=9999)
    stats = {s["handle"]: s for s in rival_stats(db, NOW - timedelta(hours=2))}
    assert set(stats) == {"rakip1", "pusholder"}
    r = stats["rakip1"]
    assert r["posts"] == 2 and r["avg_engagement"] == 200
    assert r["best_text"] == "yüksek"
    assert r["last_post_at"] == NOW - timedelta(minutes=20)
    assert stats["pusholder"]["posts"] == 1
