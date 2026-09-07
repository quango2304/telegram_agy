"""Time math for scheduled tasks. All wall-clock input is Asia/Ho_Chi_Minh;
everything stored and compared is timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter

LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# A run more than this late (stack was down) is skipped, not replayed.
GRACE_SECONDS = 600


class ScheduleError(ValueError):
    """Bad `when` / `cron` input from the tool caller."""


def parse_when(when: str) -> datetime:
    """Parse an ISO-8601 one-off time. A naive value is read as local time."""
    try:
        dt = datetime.fromisoformat(when.strip())
    except ValueError as exc:
        raise ScheduleError(f"'when' không phải ISO-8601 hợp lệ: {when!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(UTC)


def validate_cron(expr: str) -> str:
    expr = expr.strip()
    if not croniter.is_valid(expr):
        raise ScheduleError(f"cron không hợp lệ: {expr!r}")
    return expr


def next_cron_run(expr: str, after: datetime | None = None) -> datetime:
    """Next occurrence of ``expr`` strictly after ``after`` (default: now), UTC."""
    base_utc = after or datetime.now(UTC)
    base_local = base_utc.astimezone(LOCAL_TZ)
    nxt = croniter(expr, base_local).get_next(datetime)
    return nxt.astimezone(UTC)


def to_local_str(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")
