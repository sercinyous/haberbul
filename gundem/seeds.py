from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from gundem.models import Source, WatchAccount

SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"
_FIELDS = ("org", "kind", "url", "tier", "lang", "is_breaking_feed")
_WATCH_FIELDS = ("role", "tier")


def load_seeds(session: Session) -> None:
    """Upsert sources (by name) and watch accounts (by handle). Idempotent; files may be absent."""
    _load_sources(session)
    _load_watchlist(session)
    session.flush()


def _load_sources(session: Session) -> None:
    path = SEEDS_DIR / "sources.yaml"
    if not path.exists():
        return
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for row in rows:
        source = session.scalar(select(Source).where(Source.name == row["name"]))
        if source is None:
            # `enabled` is seeded only on creation so an operator's toggle survives restarts.
            source = Source(name=row["name"], enabled=row.get("enabled", True))
            session.add(source)
        for field in _FIELDS:
            if field in row:
                setattr(source, field, row[field])


def _load_watchlist(session: Session) -> None:
    path = SEEDS_DIR / "watchlist.yaml"
    if not path.exists():
        return
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for row in rows:
        account = session.scalar(select(WatchAccount).where(WatchAccount.handle == row["handle"]))
        if account is None:
            account = WatchAccount(handle=row["handle"], enabled=row.get("enabled", True))
            session.add(account)
        for field in _WATCH_FIELDS:
            if field in row:
                setattr(account, field, row[field])
    session.flush()
