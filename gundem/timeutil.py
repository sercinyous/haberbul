from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_today(tz: str, now: datetime | None = None) -> date:
    moment = now if now is not None else now_utc()
    return moment.astimezone(ZoneInfo(tz)).date()
