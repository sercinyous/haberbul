from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from gundem.models import Source

SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"
_FIELDS = ("org", "kind", "url", "tier", "lang", "is_breaking_feed")


def load_seeds(session: Session) -> None:
    """Upsert sources by name from seeds/sources.yaml. Idempotent.

    Other seed files (watchlist.yaml, Task 4) are handled by their own loaders and may be absent.
    """
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
    session.flush()
