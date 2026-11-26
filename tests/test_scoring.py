import math
from datetime import datetime, timedelta, timezone

from gundem.models import Topic
from gundem.scoring import score_topic

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def topic(**overrides) -> Topic:
    fields = dict(
        title="Konu",
        lang="tr",
        first_seen_at=NOW,
        last_seen_at=NOW,
        is_breaking=False,
        fit_score=10,
        x_velocity=300.0,
        x_velocity_prev=0.0,
        org_count=3,
        trends_hit=True,
        rival_handle=None,
        rival_eng_per_min=0.0,
        verified=True,
    )
    fields.update(overrides)
    return Topic(**fields)


def test_hot_topic():
    t = topic()
    expected = math.floor(100 * (0.30 + 0.15 + 0.10 + 0.15 + 0.10 + 0.05 + 0.10) + 0.5)
    assert expected == 95
    assert score_topic(t, NOW) == 95


def test_rival_traction_raises_score():
    t = topic(rival_handle="rakip", rival_eng_per_min=50.0)
    assert score_topic(t, NOW) == 100


def test_unverified_discount():
    t = topic(verified=False)
    assert score_topic(t, NOW) == 81


def test_missing_fit_uses_5():
    t = topic(fit_score=None)
    # fit 5 -> 0.5 * 0.30 = 0.15; hot-topic total 0.95 - 0.15 = 0.80 -> 80
    assert score_topic(t, NOW) == 80


def test_breaking_floor_90():
    t = topic(
        fit_score=None, x_velocity=0.0, x_velocity_prev=0.0, org_count=0,
        trends_hit=False, first_seen_at=NOW - timedelta(minutes=1000), verified=True,
        is_breaking=True,
    )
    assert score_topic(t, NOW) == 90
