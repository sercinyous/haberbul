from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from gundem.config import get_settings
from gundem.models import Item, Source, Topic, XSpend
from gundem.timeutil import local_today


def make_source(db, name="AA"):
    source = Source(
        name=name, org=name, kind="rss", url="https://example.com/rss",
        tier=1, lang="tr", is_breaking_feed=True, enabled=True,
    )
    db.add(source)
    db.flush()
    return source


def test_settings_defaults():
    s = get_settings()
    assert s.x_daily_budget_usd == 5.0
    assert s.x_share_breaking == 0.5
    assert s.draft_score_threshold == 60
    assert s.anthropic_model is None


def test_item_unique_per_source(db):
    source = make_source(db)
    now = datetime.now(timezone.utc)
    for _ in range(2):
        db.add(Item(
            source_id=source.id, external_id="abc", title="t", url="https://example.com/1",
            first_seen_at=now, lang="tr", is_breaking=False,
        ))
    with pytest.raises(IntegrityError):
        db.flush()


def test_topic_defaults(db):
    now = datetime.now(timezone.utc)
    topic = Topic(title="Baslik", lang="tr", first_seen_at=now, last_seen_at=now)
    db.add(topic)
    db.flush()
    db.refresh(topic)
    assert topic.status == "new"
    assert topic.verified is False
    assert topic.banned is False
    assert topic.score == 0
    assert topic.x_queries == []
    assert topic.created_at is not None


def test_xspend_unique_day_job(db):
    db.add(XSpend(day=date(2026, 11, 26), job="breaking", reads=1))
    db.flush()
    db.add(XSpend(day=date(2026, 11, 26), job="breaking", reads=2))
    with pytest.raises(IntegrityError):
        db.flush()


def test_local_today_istanbul():
    moment = datetime(2026, 11, 25, 21, 30, tzinfo=timezone.utc)
    assert local_today("Europe/Istanbul", now=moment) == date(2026, 11, 26)
