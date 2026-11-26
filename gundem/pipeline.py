"""Collection, analysis, scoring and drafting cycles that the worker schedules.

Every cycle opens its own session from deps.session_factory and commits. Slow external calls
(Claude, X) are committed one topic at a time so a single failure does not roll back the rest.
pg_notify messages are queued in the same transaction and delivered on commit.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from gundem.analysis import TopicAnalyzer
from gundem.breaking import is_breaking
from gundem.budget import XBudget
from gundem.config import Settings, get_effective_settings
from gundem.drafting import DraftWriter
from gundem.models import Draft, Item, Source, Topic, WatchAccount, XCursor
from gundem.rivals import match_rival_coverage, poll_rivals
from gundem.safety import apply_safety, detect_retraction
from gundem.scoring import score_topic
from gundem.sources.rss import RawItem, collect
from gundem.sources.x import XClient, XTweet, build_queries, build_query, engagement_per_min
from gundem.timeutil import local_today
from gundem.topics import assign_topic

log = logging.getLogger(__name__)

X_BREAKING_JOB = "breaking"
VELOCITY_JOB = "velocity"
VELOCITY_TOPICS_PER_CYCLE = 3
VELOCITY_MAX_RESULTS = 10
VELOCITY_REMEASURE = timedelta(minutes=30)
ANALYSIS_PER_CYCLE = 10
ANALYSIS_RETRY_AFTER = timedelta(minutes=15)
ANALYSIS_MAX_FAILURES = 3
RESCORE_WINDOW = timedelta(hours=24)
ACTIVE_STATUSES = ("new", "claimed")
X_TITLE_LEN = 200
_TURKISH_CHARS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")

Notifier = Callable[[Topic, Draft | None], None]


@dataclass
class Deps:
    session_factory: Callable[[], Session]
    http: httpx.Client
    x: XClient | None
    analyzer: TopicAnalyzer | None
    writer: DraftWriter | None
    notifier: Notifier | None
    settings: Settings
    # In-process only: topic_id -> (failure count, last failure time) for Claude analysis.
    analysis_failures: dict[int, tuple[int, datetime]] = field(default_factory=dict)


def notify(session: Session, type: str, topic_id: int) -> None:
    """Queue a panel event; Postgres delivers it when the session commits."""
    session.execute(
        text("SELECT pg_notify('gundem', :p)"),
        {"p": json.dumps({"type": type, "topic_id": topic_id})},
    )


def _topic_items(session: Session, topic: Topic) -> list[Item]:
    return list(session.scalars(select(Item).where(Item.topic_id == topic.id).order_by(Item.id)))


def _refresh_x_budget(deps: Deps, settings: Settings, now: datetime) -> None:
    # A fresh budget per cycle makes the daily caps roll over at local midnight.
    if isinstance(deps.x, XClient):
        deps.x.budget = XBudget(deps.session_factory, settings, local_today(settings.timezone, now))


def _breaking_topic_ids(session: Session, settings: Settings, now: datetime) -> set[int]:
    since = now - timedelta(hours=settings.topic_window_hours)
    return set(session.scalars(
        select(Topic.id).where(Topic.is_breaking.is_(True), Topic.last_seen_at >= since)
    ))


def _ingest(session: Session, items: list[Item], now: datetime) -> list[Topic]:
    """Group new items into topics, flag safety/retractions and rescore. Returns touched topics."""
    touched: dict[int, Topic] = {}
    for item in items:
        topic = assign_topic(item, session, now)
        if topic is None:
            continue
        apply_safety(topic, [item], session)
        if detect_retraction(item) and topic.status == "shared" and not topic.retracted:
            topic.retracted = True
            notify(session, "retraction", topic.id)
        touched[topic.id] = topic
    for topic in touched.values():
        score = score_topic(topic, now)
        if score != topic.score:
            topic.score = score
            notify(session, "topic", topic.id)
    session.flush()
    return list(touched.values())


def _ingest_breaking(deps: Deps, session: Session, settings: Settings, items: list[Item], now: datetime) -> list[Topic]:
    """Ingest, commit, then fast-draft and alert each topic that just became breaking."""
    before = _breaking_topic_ids(session, settings, now)
    touched = _ingest(session, items, now)
    session.commit()
    fresh = [t for t in touched if t.is_breaking and t.id not in before]
    for topic in fresh:
        draft = None
        if deps.writer is not None and not topic.banned:
            previous_error = topic.draft_error
            try:
                draft = deps.writer.write_fast(topic, _topic_items(session, topic), session)
            except Exception:
                log.exception("fast draft failed for topic %s", topic.id)
                session.rollback()
            if draft is None and topic.draft_error != previous_error:
                # Only full-draft failures gate the draft cycle; a failed fast draft is just logged.
                log.warning("fast draft failed for topic %s: %s", topic.id, topic.draft_error)
                topic.draft_error = previous_error
        notify(session, "breaking", topic.id)
        session.commit()
        if deps.notifier is not None and not topic.banned:
            try:
                deps.notifier(topic, draft)
            except Exception:
                log.exception("breaking notifier failed for topic %s", topic.id)
    return fresh


def _rss_sources(session: Session, tiers: tuple[int, ...]) -> list[Source]:
    return list(session.scalars(
        select(Source)
        .where(Source.enabled.is_(True), Source.kind.in_(("rss", "trends")), Source.tier.in_(tiers))
        .order_by(Source.id)
    ))


def run_breaking_cycle(deps: Deps, now: datetime) -> list[Topic]:
    """Tier 1 RSS. Returns the topics that became breaking in this cycle."""
    with deps.session_factory() as session:
        settings = get_effective_settings(session)
        items = collect(_rss_sources(session, (1,)), session, deps.http, now)
        return _ingest_breaking(deps, session, settings, items, now)


def _guess_lang(text_: str) -> str:
    return "tr" if _TURKISH_CHARS.search(text_) else "en"


def _x_source(session: Session, handle: str, lang: str, cache: dict[str, Source]) -> Source:
    url = f"x:{handle}"
    if url not in cache:
        source = session.scalar(select(Source).where(Source.kind == "x", Source.url == url))
        if source is None:
            source = Source(name=f"X @{handle}", org=handle, kind="x", url=url, tier=1, lang=lang,
                            is_breaking_feed=False)
            session.add(source)
            session.flush()
        cache[url] = source
    return cache[url]


def _tweet_item(session: Session, tweet: XTweet, settings: Settings, now: datetime,
                cache: dict[str, Source]) -> Item | None:
    lang = _guess_lang(tweet.text)
    source = _x_source(session, tweet.author_handle, lang, cache)
    exists = session.scalar(
        select(Item.id).where(Item.source_id == source.id, Item.external_id == tweet.id)
    )
    if exists is not None:
        return None
    raw = RawItem(
        external_id=tweet.id,
        title=tweet.text[:X_TITLE_LEN],
        summary=tweet.text,
        url=f"https://x.com/{tweet.author_handle}/status/{tweet.id}",
        published_at=tweet.created_at,
        lang=lang,
    )
    item = Item(
        source_id=source.id, external_id=raw.external_id, title=raw.title, summary=raw.summary,
        url=raw.url, published_at=raw.published_at, first_seen_at=now, lang=raw.lang,
        is_breaking=is_breaking(raw, source, now, now, settings.breaking_max_age_min),
    )
    session.add(item)
    session.flush()
    return item


def run_x_breaking_cycle(deps: Deps, now: datetime) -> list[Topic]:
    """Breaking watchlist accounts on X; each new post becomes an Item of a tier 1 x source."""
    if deps.x is None:
        return []
    with deps.session_factory() as session:
        settings = get_effective_settings(session)
        _refresh_x_budget(deps, settings, now)
        handles = list(session.scalars(
            select(WatchAccount.handle)
            .where(WatchAccount.role == "breaking", WatchAccount.enabled.is_(True))
            .order_by(WatchAccount.id)
        ))
        if not handles:
            return []
        cursor = session.scalar(select(XCursor).where(XCursor.job == X_BREAKING_JOB))
        since_id = cursor.since_id if cursor else None
        items: list[Item] = []
        newest: str | None = None
        cache: dict[str, Source] = {}
        for query in build_queries(from_handles=handles):
            tweets, newest_id = deps.x.search_recent(X_BREAKING_JOB, query, since_id=since_id)
            if newest_id and (newest is None or int(newest_id) > int(newest)):
                newest = newest_id
            for tweet in tweets:
                if not tweet.author_handle:
                    continue
                item = _tweet_item(session, tweet, settings, now, cache)
                if item is not None:
                    items.append(item)
        if newest:
            if cursor is None:
                session.add(XCursor(job=X_BREAKING_JOB, since_id=newest))
            else:
                cursor.since_id = newest
        return _ingest_breaking(deps, session, settings, items, now)


def run_gundem_cycle(deps: Deps, now: datetime) -> int:
    """Tier 2-3 RSS. Returns the number of new items."""
    with deps.session_factory() as session:
        items = collect(_rss_sources(session, (2, 3)), session, deps.http, now)
        _ingest(session, items, now)
        session.commit()
        return len(items)


def run_rival_cycle(deps: Deps, now: datetime) -> None:
    with deps.session_factory() as session:
        settings = get_effective_settings(session)
        _refresh_x_budget(deps, settings, now)
        poll_rivals(deps.x, session, now)
        session.commit()
        match_rival_coverage(session, now)
        session.commit()


def _velocity_reads_left(x) -> int | None:
    if not isinstance(x, XClient):
        return None
    budget = x.budget
    return budget.cap_reads(VELOCITY_JOB) - budget.used_reads(VELOCITY_JOB) - budget.used_reads("lookup")


def run_velocity_cycle(deps: Deps, now: datetime) -> None:
    """Measure X engagement for up to 3 high-scoring, non-breaking topics."""
    if deps.x is None:
        return
    with deps.session_factory() as session:
        settings = get_effective_settings(session)
        _refresh_x_budget(deps, settings, now)
        candidates = session.scalars(
            select(Topic.id)
            .where(
                Topic.score >= settings.draft_score_threshold,
                Topic.is_breaking.is_(False),
                Topic.status.in_(ACTIVE_STATUSES),
                Topic.banned.is_(False),
                Topic.retracted.is_(False),
                Topic.last_seen_at >= now - timedelta(hours=settings.topic_window_hours),
                func.coalesce(func.json_array_length(Topic.x_queries), 0) > 0,
                or_(Topic.x_velocity_measured_at.is_(None),
                    Topic.x_velocity_measured_at < now - VELOCITY_REMEASURE),
            )
            .order_by(Topic.score.desc(), Topic.id)
            .limit(VELOCITY_TOPICS_PER_CYCLE)
        )
        topic_ids = list(candidates)
        session.commit()
        for topic_id in topic_ids:
            topic = session.get(Topic, topic_id)
            left = _velocity_reads_left(deps.x)
            if left is not None and left < VELOCITY_MAX_RESULTS:
                # An exhausted budget returns no tweets; that must not read as zero velocity.
                log.info("velocity budget exhausted; stopping cycle")
                break
            try:
                query = build_query(list(topic.x_queries))
            except ValueError:
                log.warning("velocity query unusable for topic %s", topic.id)
                continue
            tweets, _ = deps.x.search_recent(VELOCITY_JOB, query, max_results=VELOCITY_MAX_RESULTS)
            topic.x_velocity_prev = topic.x_velocity
            topic.x_velocity = engagement_per_min(tweets, now)
            topic.x_velocity_measured_at = now
            session.commit()


def _analysis_blocked(deps: Deps, now: datetime) -> list[int]:
    """Topics to skip: 15 minutes after a failed analysis, and for good after 3 failures."""
    return [
        topic_id
        for topic_id, (count, failed_at) in deps.analysis_failures.items()
        if count >= ANALYSIS_MAX_FAILURES or now - failed_at < ANALYSIS_RETRY_AFTER
    ]


def run_analysis_and_score_cycle(deps: Deps, now: datetime) -> None:
    """Analyze unanalyzed topics (breaking first), then rescore every active recent topic."""
    with deps.session_factory() as session:
        since = now - RESCORE_WINDOW
        if deps.analyzer is not None:
            blocked = _analysis_blocked(deps, now)
            pending = list(session.scalars(
                select(Topic.id)
                .where(Topic.category.is_(None), Topic.last_seen_at >= since,
                       Topic.status.in_(ACTIVE_STATUSES),
                       Topic.id.not_in(blocked) if blocked else True)
                .order_by(Topic.is_breaking.desc(), Topic.first_seen_at.desc(), Topic.id)
                .limit(ANALYSIS_PER_CYCLE)
            ))
            session.commit()
            for topic_id in pending:
                topic = session.get(Topic, topic_id)
                try:
                    result = deps.analyzer.analyze(topic, _topic_items(session, topic))
                    session.commit()
                except Exception:
                    log.exception("analysis failed for topic %s", topic_id)
                    session.rollback()
                    result = None
                if result is None:
                    count = deps.analysis_failures.get(topic_id, (0, now))[0] + 1
                    deps.analysis_failures[topic_id] = (count, now)
                else:
                    deps.analysis_failures.pop(topic_id, None)

        topics = session.scalars(
            select(Topic)
            .where(Topic.last_seen_at >= since, Topic.status.in_(ACTIVE_STATUSES))
            .order_by(Topic.id)
        ).all()
        for topic in topics:
            flags = (topic.banned, topic.sensitive)
            apply_safety(topic, _topic_items(session, topic), session)
            score = score_topic(topic, now)
            if score != topic.score or flags != (topic.banned, topic.sensitive):
                topic.score = score
                notify(session, "topic", topic.id)
        session.commit()


def run_draft_cycle(deps: Deps, now: datetime) -> int:
    """Full drafts: breaking topics without one first, then by score. Returns drafts written."""
    if deps.writer is None:
        return 0
    with deps.session_factory() as session:
        settings = get_effective_settings(session)
        since = now - timedelta(hours=settings.topic_window_hours)
        has_full = select(Draft.id).where(Draft.topic_id == Topic.id, Draft.kind == "full").exists()
        topic_ids = list(session.scalars(
            select(Topic.id)
            .where(
                or_(Topic.score >= settings.draft_score_threshold, Topic.is_breaking.is_(True)),
                Topic.banned.is_(False),
                Topic.draft_error.is_(None),
                Topic.retracted.is_(False),
                Topic.status.in_(ACTIVE_STATUSES),
                Topic.last_seen_at >= since,
                ~has_full,
            )
            .order_by(Topic.is_breaking.desc(), Topic.score.desc(), Topic.id)
            .limit(settings.max_drafts_per_cycle)
        ))
        session.commit()
        written = 0
        for topic_id in topic_ids:
            topic = session.get(Topic, topic_id)
            try:
                items = _topic_items(session, topic)
                # A ban keyword may have been added since the last rescore.
                apply_safety(topic, items, session)
                if topic.banned:
                    notify(session, "topic", topic.id)
                    session.commit()
                    continue
                draft = deps.writer.write(topic, items, session)
                if draft is not None:
                    notify(session, "draft", topic.id)
                    written += 1
                session.commit()
            except Exception:
                log.exception("draft failed for topic %s", topic_id)
                session.rollback()
        return written
