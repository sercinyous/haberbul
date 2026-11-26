"""Rival and own X account tracking. Read-only; flushes, the caller commits."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from gundem.config import get_settings
from gundem.models import RivalPost, Topic, WatchAccount, XCursor
from gundem.sources.x import XClient, XTweet, build_queries, engagement_per_min
from gundem.topics import normalize_title, similar

JOB = "rival"
COVERAGE_SLACK = 7


def poll_rivals(x: XClient | None, session: Session, now: datetime) -> int:
    if x is None:
        return 0
    handles = list(session.scalars(
        select(WatchAccount.handle)
        .where(WatchAccount.role.in_(("rival", "own")), WatchAccount.enabled.is_(True))
        .order_by(WatchAccount.id)
    ))
    if not handles:
        return 0
    cursor = session.scalars(select(XCursor).where(XCursor.job == JOB)).first()
    since_id = cursor.since_id if cursor else None
    known = set(session.scalars(select(RivalPost.tweet_id)))
    added, newest = 0, None
    for query in build_queries(from_handles=handles):
        tweets, newest_id = x.search_recent(JOB, query, since_id=since_id, max_results=10)
        if newest_id and (newest is None or int(newest_id) > int(newest)):
            newest = newest_id
        for t in tweets:
            if t.id in known or not t.author_handle:
                continue
            known.add(t.id)
            session.add(RivalPost(
                handle=t.author_handle, tweet_id=t.id, text=t.text, created_at_x=t.created_at,
                like_count=t.like_count, repost_count=t.repost_count,
                reply_count=t.reply_count, quote_count=t.quote_count,
            ))
            added += 1
    if newest:
        if cursor is None:
            session.add(XCursor(job=JOB, since_id=newest))
        else:
            cursor.since_id = newest
    session.flush()
    return added


def match_rival_coverage(session: Session, now: datetime) -> int:
    settings = get_settings()
    since = now - timedelta(hours=settings.topic_window_hours)
    threshold = settings.topic_similarity - COVERAGE_SLACK
    topics = session.scalars(select(Topic).where(Topic.last_seen_at >= since)).all()
    own = {h.lower() for h in session.scalars(
        select(WatchAccount.handle).where(WatchAccount.role == "own"))}
    posts = session.scalars(
        select(RivalPost)
        .where(RivalPost.topic_id.is_(None), RivalPost.created_at_x >= since)
        .order_by(RivalPost.created_at_x)
    ).all()
    titles = [(topic, normalize_title(topic.title)) for topic in topics]
    linked = 0
    for post in posts:
        needle = normalize_title(post.text)
        best, best_score = None, 0
        for topic, title in titles:
            score = similar(needle, title)
            if score >= threshold and score > best_score:
                best, best_score = topic, score
        if best is None:
            continue
        post.topic_id = best.id
        linked += 1
        if post.handle.lower() in own:
            continue
        if best.rival_at is None or post.created_at_x < best.rival_at:
            best.rival_handle = post.handle
            best.rival_at = post.created_at_x
            best.rival_text = post.text
            best.rival_eng_per_min = engagement_per_min([_as_tweet(post)], now)
    session.flush()
    return linked


def _as_tweet(post: RivalPost):
    return XTweet(post.tweet_id, post.handle, post.text, post.created_at_x,
                  post.like_count, post.repost_count, post.reply_count, post.quote_count)


def _engagement(post: RivalPost) -> int:
    return post.like_count + post.repost_count + post.reply_count + post.quote_count


def rival_stats(session: Session, since: datetime) -> list[dict]:
    posts = session.scalars(
        select(RivalPost).where(RivalPost.created_at_x >= since).order_by(RivalPost.created_at_x)
    ).all()
    by_handle: dict[str, list[RivalPost]] = {}
    for p in posts:
        by_handle.setdefault(p.handle, []).append(p)
    stats = []
    for handle, items in by_handle.items():
        best = max(items, key=_engagement)
        stats.append({
            "handle": handle,
            "posts": len(items),
            "avg_engagement": sum(_engagement(p) for p in items) / len(items),
            "best_text": best.text,
            "last_post_at": max(p.created_at_x for p in items),
        })
    return sorted(stats, key=lambda s: s["posts"], reverse=True)
