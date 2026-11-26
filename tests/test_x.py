from datetime import date, datetime, timezone

import httpx
import pytest
import respx
from sqlalchemy.orm import sessionmaker

from gundem.budget import XBudget
from gundem.config import get_settings
from gundem.sources.x import (
    XClient,
    XTweet,
    build_queries,
    build_query,
    controversy,
    engagement_per_min,
)

URL = "https://api.x.com/2/tweets/search/recent"
TODAY = date(2026, 3, 10)


def _client(db):
    budget = XBudget(sessionmaker(bind=db.get_bind(), expire_on_commit=False), get_settings(), TODAY)
    http = httpx.Client()
    return XClient("tok", budget, http), budget


def _payload(n=3):
    return {
        "data": [
            {
                "id": str(100 + i),
                "author_id": "u1",
                "text": f"t{i}",
                "created_at": "2026-03-10T10:00:00.000Z",
                "public_metrics": {
                    "like_count": 10,
                    "retweet_count": 2,
                    "reply_count": 3,
                    "quote_count": 1,
                },
            }
            for i in range(n)
        ],
        "includes": {"users": [{"id": "u1", "username": "Reuters"}]},
        "meta": {"result_count": n, "newest_id": "102"},
    }


def test_build_query_parenthesizes():
    assert (
        build_query(["deprem", "istanbul"])
        == "(deprem OR istanbul) -is:retweet -is:reply (lang:tr OR lang:en)"
    )
    assert build_query([], ["a", "b"]).startswith("(from:a OR from:b) -is:retweet")


def test_build_query_empty_and_too_long():
    with pytest.raises(ValueError):
        build_query([])
    with pytest.raises(ValueError):
        build_query([], ["h" * 20] * 40)


def test_build_queries_splits_handles():
    handles = [f"account{i:03d}" for i in range(80)]
    qs = build_queries(from_handles=handles)
    assert len(qs) > 1
    assert all(len(q) <= 512 for q in qs)
    joined = " ".join(qs)
    assert all(f"from:{h}" in joined for h in handles)


@respx.mock
def test_search_parses_metrics_and_newest_id(db):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=_payload()))
    client, budget = _client(db)
    tweets, newest = client.search_recent("breaking", "q", since_id="50")
    assert newest == "102"
    assert len(tweets) == 3
    t = tweets[0]
    assert t.author_handle == "Reuters"
    assert (t.like_count, t.repost_count, t.reply_count, t.quote_count) == (10, 2, 3, 1)
    assert t.created_at == datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer tok"
    p = req.url.params
    assert p["since_id"] == "50"
    assert p["max_results"] == "10"
    assert p["tweet.fields"] == "created_at,public_metrics,author_id"
    assert p["expansions"] == "author_id"
    assert p["user.fields"] == "username"


@respx.mock
def test_reserve_and_refund_unused_reads(db):
    respx.get(URL).mock(return_value=httpx.Response(200, json=_payload(3)))
    client, budget = _client(db)
    client.search_recent("breaking", "q")
    assert budget.used_reads("breaking") == 3


@respx.mock
def test_budget_exhausted_blocks_without_http(db):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=_payload()))
    client, budget = _client(db)
    assert budget.try_reserve("rival", 250)
    assert client.search_recent("rival", "q") == ([], None)
    assert not route.called
    assert budget.used_reads("rival") == 250


@respx.mock
def test_unparseable_200_keeps_reservation(db):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"<html>not json</html>"))
    client, budget = _client(db)
    assert client.search_recent("breaking", "q") == ([], None)
    assert budget.used_reads("breaking") == 10


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limited_refunds_all(db, status):
    respx.get(URL).mock(return_value=httpx.Response(status))
    client, budget = _client(db)
    assert client.search_recent("breaking", "q") == ([], None)
    assert budget.used_reads("breaking") == 0


@respx.mock
def test_network_error_refunds_all(db):
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    client, budget = _client(db)
    assert client.search_recent("breaking", "q") == ([], None)
    assert budget.used_reads("breaking") == 0


def _tw(created, like=0, repost=0, reply=0, quote=0):
    return XTweet("1", "a", "t", created, like, repost, reply, quote)


def test_engagement_per_min():
    now = datetime(2026, 3, 10, 10, 10, tzinfo=timezone.utc)
    old = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    tweets = [_tw(old, like=200), _tw(old, repost=100, reply=100), _tw(now, quote=200)]
    assert engagement_per_min(tweets, now) == 60.0
    assert engagement_per_min([], now) == 0.0


def test_controversy():
    now = datetime(2026, 3, 10, tzinfo=timezone.utc)
    tweets = [_tw(now, like=10, reply=15, quote=5)]
    assert controversy(tweets) == 2.0
    assert controversy([_tw(now, reply=3)]) == 3.0


def test_watchlist_seeds_idempotent_and_keep_toggle(db):
    from sqlalchemy import select

    from gundem.models import WatchAccount
    from gundem.seeds import load_seeds

    load_seeds(db)
    rows = db.scalars(select(WatchAccount)).all()
    by = {r.handle: r for r in rows}
    assert {"Reuters", "AP", "AFP", "AFPTurkce"} <= set(by)
    assert all(by[h].role == "breaking" and by[h].tier == 1 for h in ("Reuters", "AP"))
    assert not any(r.role == "rival" and r.enabled for r in rows)
    by["Reuters"].enabled = False
    db.flush()
    load_seeds(db)
    assert len(db.scalars(select(WatchAccount)).all()) == len(rows)
    assert db.scalar(select(WatchAccount).where(WatchAccount.handle == "Reuters")).enabled is False
