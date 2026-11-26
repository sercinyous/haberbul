import calendar
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from gundem.breaking import is_breaking
from gundem.config import get_settings
from gundem.models import Item, Source

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
TIMEOUT = 10.0
MAX_WORKERS = 8
_TRACKING_PARAMS = {"fbclid", "gclid"}


@dataclass
class RawItem:
    external_id: str
    title: str
    summary: str | None
    url: str
    published_at: datetime | None
    lang: str


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _entry_time(entry, now: datetime) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    moment = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None if moment > now else moment


def fetch_rss(source: "Source | _Snapshot", client: httpx.Client) -> list[RawItem] | None:
    """None = 304 Not Modified."""
    headers = {"User-Agent": USER_AGENT}
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified
    resp = client.get(source.url, headers=headers, timeout=TIMEOUT, follow_redirects=True)
    if resp.status_code == 304:
        return None
    resp.raise_for_status()
    source.etag = resp.headers.get("etag")
    source.last_modified = resp.headers.get("last-modified")

    now = datetime.now(timezone.utc)
    feed = feedparser.parse(resp.content)
    items: list[RawItem] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = entry.get("link") or ""
        if not title or not (link or entry.get("id")):
            continue
        url = normalize_url(link) if link else entry.get("id")
        external_id = entry.get("id") or url
        items.append(
            RawItem(
                external_id=external_id[:500],
                title=title,
                summary=(entry.get("summary") or None),
                url=url,
                published_at=_entry_time(entry, now),
                lang=source.lang,
            )
        )
    return items


def _fetch_one(snapshot: "_Snapshot", client: httpx.Client):
    try:
        return fetch_rss(snapshot, client), None
    except Exception as exc:  # one bad source must not stop the others
        return None, f"{type(exc).__name__}: {exc}"[:500]


def collect(sources: list[Source], session: Session, client: httpx.Client, now: datetime) -> list[Item]:
    """Fetch in threads on plain snapshots; every ORM/DB write happens in the calling thread."""
    max_age = get_settings().breaking_max_age_min
    targets = [s for s in sources if s.kind in ("rss", "trends")]
    snapshots = [_Snapshot(s) for s in targets]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda snap: _fetch_one(snap, client), snapshots))

    new_items: list[Item] = []
    for source, snap, (raws, error) in zip(targets, snapshots, results):
        if error:
            source.last_error = error
            log.warning("source %s failed: %s", source.name, error)
            continue
        source.etag, source.last_modified = snap.etag, snap.last_modified
        source.last_ok_at = now
        source.last_error = None
        if not raws:
            continue
        existing = set(
            session.scalars(
                select(Item.external_id).where(
                    Item.source_id == source.id,
                    Item.external_id.in_([r.external_id for r in raws]),
                )
            )
        )
        for raw in raws:
            if raw.external_id in existing:
                continue
            existing.add(raw.external_id)
            item = Item(
                source_id=source.id,
                external_id=raw.external_id,
                title=raw.title,
                summary=raw.summary,
                url=raw.url,
                published_at=raw.published_at,
                first_seen_at=now,
                lang=raw.lang,
                is_breaking=is_breaking(raw, source, now, now, max_age),
            )
            session.add(item)
            new_items.append(item)
    session.flush()
    return new_items


class _Snapshot:
    """Plain copy of the Source fields fetch_rss reads/writes, safe for worker threads."""

    def __init__(self, source: Source):
        self.url = source.url
        self.lang = source.lang
        self.etag = source.etag
        self.last_modified = source.last_modified
