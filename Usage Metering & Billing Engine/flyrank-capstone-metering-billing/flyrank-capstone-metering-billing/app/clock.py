"""Single source of 'now' (naive UTC) so tests can freeze/move time."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def period_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Billing period = UTC calendar month: [start, next_start)."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, nxt
