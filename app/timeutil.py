from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_local() -> date:
    """تاريخ اليوم بتوقيت الرياض (للاستحقاق والتحقق من تاريخ بداية العقد)."""
    return datetime.now(RIYADH).date()


def local_dt(value: datetime | None) -> str:
    """التخزين دايماً UTC، والعرض بتوقيت الرياض."""
    return value.astimezone(RIYADH).strftime("%Y-%m-%d %H:%M") if value else ""