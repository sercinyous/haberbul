from datetime import datetime, timedelta, timezone

import pytest

from gundem.breaking import is_breaking
from gundem.models import Source
from gundem.sources.rss import RawItem

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def raw(title="Normal haber", age_min: int | None = 5) -> RawItem:
    pub = None if age_min is None else NOW - timedelta(minutes=age_min)
    return RawItem("id", title, None, "https://e.com", pub, "tr")


def src(tier=1, feed=False) -> Source:
    return Source(name="s", org="o", kind="rss", url="u", tier=tier, lang="tr", is_breaking_feed=feed)


def test_breaking_tier1_fresh_flagged():
    assert is_breaking(raw(), src(feed=True), NOW, NOW, 30)
    assert is_breaking(raw("SON DAKİKA: deprem"), src(), NOW, NOW, 30)


@pytest.mark.parametrize("title", ["son dakika gelişme", "FLAŞ haber", "Flaş!", "Breaking: x", "URGENT update"])
def test_keywords_case_insensitive(title):
    assert is_breaking(raw(title), src(), NOW, NOW, 30)


def test_plain_title_on_normal_feed_not_breaking():
    assert not is_breaking(raw("Hava durumu"), src(), NOW, NOW, 30)
    assert not is_breaking(raw("Flash drive satışları"), src(), NOW, NOW, 30)


def test_old_item_is_not_breaking():
    assert not is_breaking(raw(age_min=45), src(feed=True), NOW, NOW, 30)


def test_stale_first_seen_is_not_breaking():
    assert not is_breaking(raw(age_min=5), src(feed=True), NOW - timedelta(minutes=40), NOW, 30)


def test_undated_item_not_breaking():
    assert not is_breaking(raw(age_min=None), src(feed=True), NOW, NOW, 30)


@pytest.mark.parametrize("tier", [2, 3])
def test_tier2_never_breaking(tier):
    assert not is_breaking(raw("SON DAKİKA"), src(tier=tier, feed=True), NOW, NOW, 30)
