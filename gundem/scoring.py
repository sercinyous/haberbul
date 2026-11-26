import math
from datetime import datetime

from gundem.models import Topic

# Weights of the viral score. They sum to 1.0 for a fully active, verified topic.
W_FIT = 0.30
W_VELOCITY = 0.15
W_ACCEL = 0.10
W_SPREAD = 0.15
W_TRENDS = 0.10
W_RIVAL = 0.10
W_RECENCY = 0.10

DEFAULT_FIT = 5
UNVERIFIED_FACTOR = 0.85
BREAKING_FLOOR = 90

VELOCITY_CAP = 300.0
ACCEL_MIN_BASE = 30.0
SPREAD_CAP_ORGS = 3
RIVAL_CAP_ENG_PER_MIN = 50.0
RECENCY_WINDOW_MIN = 180.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def score_topic(topic: Topic, now: datetime) -> int:
    """Viral score in 0..100. Pure: the caller stores the result in topic.score."""
    age_min = (now - topic.first_seen_at).total_seconds() / 60
    fit_n = (topic.fit_score if topic.fit_score is not None else DEFAULT_FIT) / 10
    accel_n = _clamp(
        (topic.x_velocity - topic.x_velocity_prev) / max(ACCEL_MIN_BASE, topic.x_velocity_prev),
        0.0,
        1.0,
    )
    velocity_n = min(1.0, topic.x_velocity / VELOCITY_CAP)
    spread_n = min(1.0, topic.org_count / SPREAD_CAP_ORGS)
    trends_n = 1.0 if topic.trends_hit else 0.0
    if topic.rival_handle is None:
        rival_n = 0.5
    else:
        rival_n = 0.5 + 0.5 * min(1.0, topic.rival_eng_per_min / RIVAL_CAP_ENG_PER_MIN)
    recency_n = max(0.0, 1 - age_min / RECENCY_WINDOW_MIN)

    raw = (
        W_FIT * fit_n
        + W_VELOCITY * velocity_n
        + W_ACCEL * accel_n
        + W_SPREAD * spread_n
        + W_TRENDS * trends_n
        + W_RIVAL * rival_n
        + W_RECENCY * recency_n
    )
    if not topic.verified:
        raw *= UNVERIFIED_FACTOR

    score = math.floor(100 * raw + 0.5)
    if topic.is_breaking:
        score = max(score, BREAKING_FLOOR)
    return score
