import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from gundem.models import Source

if TYPE_CHECKING:
    from gundem.sources.rss import RawItem

# Titles are folded with upper() first: Turkish i/I/İ/ı make str.lower() unreliable.
_KEYWORDS = re.compile(r"\b(SON DAKIKA|FLAS|BREAKING|URGENT)\b")


def _has_keyword(title: str) -> bool:
    folded = title.upper().replace("İ", "I").replace("Ş", "S")
    return bool(_KEYWORDS.search(folded))


def is_breaking(raw: "RawItem", source: Source, first_seen: datetime, now: datetime, max_age_min: int) -> bool:
    if source.tier != 1 or raw.published_at is None:
        return False
    window = timedelta(minutes=max_age_min)
    if now - raw.published_at > window or now - first_seen > window:
        return False
    return source.is_breaking_feed or _has_keyword(raw.title)
