from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx
from sqlalchemy import select

from gundem.models import Item, Source
from gundem.seeds import load_seeds
from gundem.sources.rss import collect, fetch_rss, normalize_url

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_source(db, name="AA", url="https://feed.test/aa", **kw) -> Source:
    src = Source(name=name, org=kw.pop("org", name), kind="rss", url=url, tier=kw.pop("tier", 1),
                 lang="tr", **kw)
    db.add(src)
    db.flush()
    return src


def rss_xml(entries) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><guid>{g}</guid>"
        f"<pubDate>{p}</pubDate></item>"
        for t, l, g, p in entries
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


def http_date(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


@respx.mock
def test_fetch_rss_parses_items(db):
    src = make_source(db)
    respx.get(src.url).respond(200, content=(FIX / "basic.xml").read_bytes(),
                               headers={"ETag": '"v1"', "Last-Modified": "Mon, 01 Jan 2024 10:00:00 GMT"})
    with httpx.Client() as client:
        items = fetch_rss(src, client)
    assert [i.title for i in items] == ["Test haberi bir", "Test haberi iki"]
    assert items[0].external_id == "g1"
    assert items[0].url == "https://example.com/a?id=1"
    assert items[0].published_at == datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert src.etag == '"v1"'
    assert src.last_modified == "Mon, 01 Jan 2024 10:00:00 GMT"
    assert "Mozilla" in respx.calls.last.request.headers["user-agent"]


@respx.mock
def test_windows_1254_feed_decoded(db):
    src = make_source(db)
    raw = (FIX / "iso_8859_9.xml").read_bytes()
    assert b'encoding="ISO-8859-9"' in raw
    respx.get(src.url).respond(200, content=raw)
    with httpx.Client() as client:
        items = fetch_rss(src, client)
    assert "ğüşıöç" in items[0].title
    assert items[0].title.startswith("Çağlayan")


@respx.mock
def test_not_modified_returns_none(db):
    src = make_source(db, etag='"v1"', last_modified="Mon, 01 Jan 2024 10:00:00 GMT")
    route = respx.get(src.url).respond(304)
    with httpx.Client() as client:
        assert fetch_rss(src, client) is None
    sent = route.calls.last.request.headers
    assert sent["if-none-match"] == '"v1"'
    assert sent["if-modified-since"] == "Mon, 01 Jan 2024 10:00:00 GMT"
    assert src.etag == '"v1"'


def test_normalize_url_strips_tracking():
    assert normalize_url("https://a.com/x?utm_source=t&id=5&fbclid=z&gclid=q&utm_medium=m#top") == "https://a.com/x?id=5"
    assert normalize_url("https://a.com/x#top") == "https://a.com/x"


@respx.mock
def test_future_date_cleared(db):
    src = make_source(db)
    future = datetime.now(timezone.utc) + timedelta(days=2)
    respx.get(src.url).respond(200, content=rss_xml([("t", "https://e.com/1", "i1", http_date(future))]))
    with httpx.Client() as client:
        items = fetch_rss(src, client)
    assert items[0].published_at is None


@respx.mock
def test_external_id_falls_back_to_normalized_url(db):
    src = make_source(db)
    xml = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title><item><title>t</title><link>https://e.com/1?utm_source=a</link></item></channel></rss>'
    respx.get(src.url).respond(200, content=xml)
    with httpx.Client() as client:
        items = fetch_rss(src, client)
    assert items[0].external_id == "https://e.com/1"


@respx.mock
def test_collect_dedupes(db):
    src = make_source(db)
    respx.get(src.url).respond(200, content=(FIX / "basic.xml").read_bytes())
    with httpx.Client() as client:
        first = collect([src], db, client, NOW)
        second = collect([src], db, client, NOW + timedelta(minutes=5))
    assert len(first) == 2 and second == []
    assert db.scalar(select(Item.id).limit(1)) is not None
    assert len(db.scalars(select(Item)).all()) == 2
    assert all(i.first_seen_at == NOW for i in first)
    assert src.last_ok_at == NOW + timedelta(minutes=5)
    assert src.last_error is None


@respx.mock
def test_collect_all_skips_failing_source(db):
    bad = make_source(db, "Bad", "https://feed.test/bad")
    good = make_source(db, "Good", "https://feed.test/good")
    respx.get(bad.url).respond(500)
    respx.get(good.url).respond(200, content=(FIX / "basic.xml").read_bytes())
    with httpx.Client() as client:
        items = collect([bad, good], db, client, NOW)
    assert len(items) == 2 and all(i.source_id == good.id for i in items)
    assert "500" in bad.last_error and bad.last_ok_at is None
    assert good.last_error is None and good.last_ok_at == NOW


@respx.mock
def test_collect_marks_breaking_and_skips_x_sources(db):
    src = make_source(db, is_breaking_feed=True)
    x = Source(name="X", org="X", kind="x", url="x", tier=1, lang="tr")
    db.add(x)
    db.flush()
    fresh = http_date(NOW - timedelta(minutes=5))
    respx.get(src.url).respond(200, content=rss_xml([("Haber", "https://e.com/1", "i1", fresh)]))
    with httpx.Client() as client:
        items = collect([src, x], db, client, NOW)
    assert [i.is_breaking for i in items] == [True]


def test_load_seeds_idempotent(db):
    load_seeds(db)
    count = len(db.scalars(select(Source)).all())
    assert count >= 10
    aa = db.scalar(select(Source).where(Source.org == "AA", Source.is_breaking_feed.is_(True)))
    assert aa is not None and aa.tier == 1
    load_seeds(db)
    assert len(db.scalars(select(Source)).all()) == count
    assert db.scalar(select(Source).where(Source.kind == "trends")).tier == 3
