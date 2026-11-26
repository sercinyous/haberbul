import math
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

    Never commits: the caller owns the transaction.
    """

    def __init__(self, session: Session, settings: Settings, today: date):
        self.session = session
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
        reads = self.session.scalar(
            select(XSpend.reads).where(XSpend.day == self.today, XSpend.job == job)
        )
        return reads or 0

    def total_used_usd(self) -> float:
        total = sum(self.used_reads(job) for job in _JOBS)
        return total * self.settings.x_cost_per_read_usd

    def total_cap_usd(self) -> float:
        return self.settings.x_daily_budget_usd

    def _lock_row(self, job: str) -> XSpend:
        self.session.execute(
            insert(XSpend)
            .values(day=self.today, job=job, reads=0)
            .on_conflict_do_nothing(index_elements=["day", "job"])
        )
        return self.session.execute(
            select(XSpend)
            .where(XSpend.day == self.today, XSpend.job == job)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

    def try_reserve(self, job: str, reads: int) -> bool:
        row = self._lock_row(job)
        if job in _POOL_JOBS:
            # Lock the sibling row too, in fixed order, so the shared pool is race-free.
            rows = {j: self._lock_row(j) for j in _POOL_JOBS}
            row = rows[job]
            used = sum(r.reads for r in rows.values())
            allowed = used + reads <= self._share_cap("velocity")
        else:
            allowed = row.reads + reads <= self._share_cap(job)
        if not allowed:
            return False
        row.reads += reads
        self.session.flush()
        return True

    def refund(self, job: str, reads: int) -> None:
        row = self._lock_row(job)
        row.reads = max(0, row.reads - reads)
        self.session.flush()
