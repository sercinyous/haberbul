import math
from collections.abc import Callable
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gundem.config import Settings
from gundem.models import XSpend

_JOBS = ("breaking", "rival", "velocity", "lookup")
# lookup draws from the velocity share: the two jobs form one pool.
_POOL_JOBS = ("velocity", "lookup")


class XBudget:
    """Per-job daily read budget. Stores read counts; USD is derived.

    Every operation opens its own short session and commits at once, so a reservation is
    visible to other workers immediately, no row lock is held across an HTTP call, and a
    caller's rollback cannot lose spend that X already billed.
    """

    def __init__(self, session_factory: Callable[[], Session], settings: Settings, today: date):
        self.session_factory = session_factory
        self.settings = settings
        self.today = today

    def _share(self, job: str) -> float:
        s = self.settings
        shares = {
            "breaking": s.x_share_breaking,
            "rival": s.x_share_rival,
            "velocity": s.x_share_velocity,
            "lookup": s.x_share_velocity,
        }
        return shares[job]

    def _share_cap(self, job: str) -> int:
        total = self.settings.x_daily_budget_usd / self.settings.x_cost_per_read_usd
        return math.floor(total * self._share(job) + 1e-9)

    def cap_reads(self, job: str) -> int:
        if job == "lookup":
            return max(0, self._share_cap("velocity") - self.used_reads("velocity"))
        return self._share_cap(job)

    def used_reads(self, job: str) -> int:
        with self.session_factory() as session:
            reads = session.scalar(
                select(XSpend.reads).where(XSpend.day == self.today, XSpend.job == job)
            )
        return reads or 0

    def total_used_usd(self) -> float:
        total = sum(self.used_reads(job) for job in _JOBS)
        return total * self.settings.x_cost_per_read_usd

    def total_cap_usd(self) -> float:
        return self.settings.x_daily_budget_usd

    def _lock_row(self, session: Session, job: str) -> XSpend:
        session.execute(
            insert(XSpend)
            .values(day=self.today, job=job, reads=0)
            .on_conflict_do_nothing(index_elements=["day", "job"])
        )
        return session.execute(
            select(XSpend)
            .where(XSpend.day == self.today, XSpend.job == job)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

    def try_reserve(self, job: str, reads: int) -> bool:
        with self.session_factory() as session:
            if job in _POOL_JOBS:
                # Always lock in the fixed _POOL_JOBS order so velocity/lookup cannot deadlock.
                rows = {j: self._lock_row(session, j) for j in _POOL_JOBS}
                row = rows[job]
                allowed = sum(r.reads for r in rows.values()) + reads <= self._share_cap("velocity")
            else:
                row = self._lock_row(session, job)
                allowed = row.reads + reads <= self._share_cap(job)
            if allowed:
                row.reads += reads
            session.commit()
        return allowed

    def refund(self, job: str, reads: int) -> None:
        with self.session_factory() as session:
            row = self._lock_row(session, job)
            row.reads = max(0, row.reads - reads)
            session.commit()
