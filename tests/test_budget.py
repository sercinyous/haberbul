from datetime import date, datetime, timezone

from sqlalchemy import select

from gundem.budget import XBudget
from gundem.config import get_settings
from gundem.models import XSpend
from gundem.timeutil import local_today


def _budget(db, today=date(2026, 3, 10)):
    return XBudget(db, get_settings(), today)


def test_cap_split(db):
    b = _budget(db)
    assert b.cap_reads("breaking") == 500
    assert b.cap_reads("rival") == 250
    assert b.cap_reads("velocity") == 250
    assert b.total_cap_usd() == 5.0


def test_reserve_creates_row_and_blocks_over_cap(db):
    b = _budget(db)
    assert b.try_reserve("rival", 250) is True
    assert b.used_reads("rival") == 250
    assert b.try_reserve("rival", 1) is False
    assert b.used_reads("rival") == 250
    assert b.total_used_usd() == 250 * 0.005
    assert db.scalar(select(XSpend).where(XSpend.job == "rival")).reads == 250


def test_refund_never_goes_negative(db):
    b = _budget(db)
    assert b.try_reserve("breaking", 10)
    b.refund("breaking", 4)
    assert b.used_reads("breaking") == 6
    b.refund("breaking", 100)
    assert b.used_reads("breaking") == 0


def test_lookup_shares_velocity_pool(db):
    b = _budget(db)
    assert b.try_reserve("velocity", 200)
    assert b.cap_reads("lookup") == 50
    assert b.try_reserve("lookup", 50)
    assert b.try_reserve("lookup", 1) is False
    assert b.try_reserve("velocity", 1) is False


def test_budget_resets_at_istanbul_midnight(db):
    s = get_settings()
    late = datetime(2026, 3, 10, 20, 59, tzinfo=timezone.utc)  # 23:59 Istanbul
    after = datetime(2026, 3, 10, 21, 1, tzinfo=timezone.utc)  # 00:01 next day
    day1 = local_today(s.timezone, late)
    day2 = local_today(s.timezone, after)
    assert day1 != day2
    b1 = XBudget(db, s, day1)
    assert b1.try_reserve("breaking", 500)
    assert b1.try_reserve("breaking", 1) is False
    b2 = XBudget(db, s, day2)
    assert b2.used_reads("breaking") == 0
    assert b2.try_reserve("breaking", 10) is True
