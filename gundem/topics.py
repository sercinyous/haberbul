import re
from datetime import datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from gundem.config import get_settings
from gundem.models import Item, Source, Topic

_PREFIX = re.compile(r"^(?:son\s+dakika|flaş|flas|breaking|video|foto)\b[\s:\-–—|!.]*")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
SUMMARY_DUPLICATE_RATIO = 90


def _fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def normalize_title(title: str) -> str:
    text = _fold(title).strip()
    while True:
        stripped = _PREFIX.sub("", text)
        if stripped == text:
            break
        text = stripped
    return _SPACES.sub(" ", _PUNCT.sub("", text)).strip()


def similar(a: str, b: str) -> int:
    na, nb = len(a.split()), len(b.split())
    if na < 3 or nb < 3:
        return 0
    if na >= 4 and nb >= 4:
        return int(fuzz.token_set_ratio(a, b))
    return int(fuzz.token_sort_ratio(a, b))


def _best_topic(item: Item, session: Session, now: datetime) -> Topic | None:
    settings = get_settings()
    since = now - timedelta(hours=settings.topic_window_hours)
    candidates = session.scalars(
        select(Topic).where(Topic.lang == item.lang, Topic.last_seen_at >= since)
    ).all()
    needle = normalize_title(item.title)
    best, best_score = None, 0
    for topic in candidates:
        score = similar(needle, normalize_title(topic.title))
        if score >= settings.topic_similarity and score > best_score:
            best, best_score = topic, score
    return best


def assign_topic(item: Item, session: Session, now: datetime) -> Topic | None:
    source = session.get(Source, item.source_id)
    topic = _best_topic(item, session, now)
    if topic is None:
        if source.tier >= 3:
            return None
        topic = Topic(
            title=item.title, lang=item.lang,
            first_seen_at=item.first_seen_at, last_seen_at=now,
        )
        session.add(topic)
        session.flush()
    if source.tier <= 2:
        topic.last_seen_at = max(topic.last_seen_at, now)
    if item.is_breaking:
        topic.is_breaking = True
    if source.kind == "trends":
        topic.trends_hit = True
    item.topic_id = topic.id
    session.flush()
    recompute_verification(topic, session)
    return topic


def _summary_key(summary: str | None) -> str:
    return _SPACES.sub(" ", _PUNCT.sub("", _fold(summary or ""))).strip()


def recompute_verification(topic: Topic, session: Session) -> None:
    rows = session.execute(
        select(Source.org, Item.summary)
        .join(Item, Item.source_id == Source.id)
        .where(Item.topic_id == topic.id, Source.tier <= 2)
    ).all()
    summaries: dict[str, list[str]] = {}
    for org, summary in rows:
        keys = summaries.setdefault(org, [])
        key = _summary_key(summary)
        if key:
            keys.append(key)

    parent = {org: org for org in summaries}

    def find(org: str) -> str:
        while parent[org] != org:
            parent[org] = parent[parent[org]]
            org = parent[org]
        return org

    orgs = list(summaries)
    for i, a in enumerate(orgs):
        for b in orgs[i + 1:]:
            if find(a) == find(b):
                continue
            if any(
                fuzz.ratio(x, y) >= SUMMARY_DUPLICATE_RATIO
                for x in summaries[a] for y in summaries[b]
            ):
                parent[find(a)] = find(b)

    topic.org_count = len({find(org) for org in orgs})
    topic.verified = topic.org_count >= 2 or topic.verified_by is not None
    session.flush()


def split_item(item_id: int, session: Session, now: datetime) -> Topic:
    item = session.get(Item, item_id)
    old = session.get(Topic, item.topic_id) if item.topic_id else None
    topic = Topic(
        title=item.title, lang=item.lang,
        first_seen_at=item.first_seen_at, last_seen_at=now,
        is_breaking=item.is_breaking,
    )
    session.add(topic)
    session.flush()
    item.topic_id = topic.id
    session.flush()
    if old is not None:
        recompute_verification(old, session)
    recompute_verification(topic, session)
    return topic
