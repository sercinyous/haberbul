"""Single worker process: runs every collection/analysis/drafting cycle on a schedule."""

import logging
from collections.abc import Callable
from datetime import datetime

import anthropic
import httpx
from apscheduler.schedulers.blocking import BlockingScheduler

from gundem.analysis import TopicAnalyzer
from gundem.budget import XBudget
from gundem.config import Settings, get_settings
from gundem.db import get_sessionmaker, session_scope
from gundem.drafting import DraftWriter
from gundem.pipeline import (
    Deps,
    run_analysis_and_score_cycle,
    run_breaking_cycle,
    run_draft_cycle,
    run_gundem_cycle,
    run_rival_cycle,
    run_velocity_cycle,
    run_x_breaking_cycle,
)
from gundem.seeds import load_seeds
from gundem.sources.x import XClient
from gundem.timeutil import local_today, now_utc

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 15.0
Cycle = Callable[[Deps, datetime], object]


def build_jobs() -> list[tuple[str, Cycle, int]]:
    """(job id, cycle, interval in seconds)."""
    return [
        ("breaking_rss", run_breaking_cycle, 60),
        ("x_breaking", run_x_breaking_cycle, 90),
        ("gundem_rss", run_gundem_cycle, 300),
        ("rivals", run_rival_cycle, 900),
        ("velocity", run_velocity_cycle, 600),
        ("analysis_score", run_analysis_and_score_cycle, 60),
        ("drafts", run_draft_cycle, 30),
    ]


def build_deps(settings: Settings) -> Deps:
    """Wire clients from settings; a missing key or model leaves that field None and its step is skipped."""
    session_factory = get_sessionmaker()
    http = httpx.Client(timeout=HTTP_TIMEOUT)

    x = None
    if settings.x_bearer_token:
        budget = XBudget(session_factory, settings, local_today(settings.timezone))
        x = XClient(settings.x_bearer_token, budget, http)
    else:
        log.warning("X_BEARER_TOKEN missing: X breaking, rival and velocity reads are skipped")

    analyzer = writer = None
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY missing: topic analysis and drafting are skipped")
    else:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        if settings.anthropic_fast_model:
            analyzer = TopicAnalyzer(client, settings.anthropic_fast_model)
        else:
            log.warning("ANTHROPIC_FAST_MODEL missing: topic analysis is skipped")
        if settings.anthropic_model and settings.anthropic_fast_model:
            writer = DraftWriter(client, settings.anthropic_model, settings.anthropic_fast_model)
        else:
            log.warning("ANTHROPIC_MODEL or ANTHROPIC_FAST_MODEL missing: drafting is skipped")

    return Deps(
        session_factory=session_factory, http=http, x=x, analyzer=analyzer, writer=writer,
        notifier=None, settings=settings,
    )


def guarded(name: str, cycle: Cycle, deps: Deps) -> Callable[[], None]:
    def job() -> None:
        try:
            cycle(deps, now_utc())
        except Exception:
            log.exception("job %s failed", name)

    return job


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    with session_scope() as session:
        load_seeds(session)
    deps = build_deps(settings)
    scheduler = BlockingScheduler(timezone=settings.timezone)
    start = datetime.now(scheduler.timezone)
    for name, cycle, seconds in build_jobs():
        scheduler.add_job(
            guarded(name, cycle, deps), "interval", seconds=seconds, id=name, name=name,
            max_instances=1, coalesce=True, next_run_time=start,
        )
    log.info("worker started with %d jobs", len(build_jobs()))
    try:
        scheduler.start()
    finally:
        deps.http.close()


if __name__ == "__main__":
    main()
