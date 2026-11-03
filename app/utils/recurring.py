"""Recurring schedule math — pure."""
import calendar
from datetime import date, timedelta

FREQUENCIES = {"weekly": "Weekly", "biweekly": "Every 2 weeks", "monthly": "Monthly",
               "quarterly": "Quarterly", "yearly": "Yearly"}


def _add_months(d: date, n: int, anchor_day: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(anchor_day, calendar.monthrange(y, m)[1]))


def next_occurrence(iso: str, freq: str, anchor_day: int | None = None) -> str:
    """The date after `iso` for a schedule. Month-based schedules keep their day of
    month (anchor_day), clamped to short months: Jan 31 → Feb 28 → Mar 31."""
    d = date.fromisoformat(iso)
    anchor = anchor_day or d.day
    if freq == "weekly":
        return (d + timedelta(days=7)).isoformat()
    if freq == "biweekly":
        return (d + timedelta(days=14)).isoformat()
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(freq, 1)
    return _add_months(d, months, anchor).isoformat()


def due_occurrences(next_date: str, until: str, freq: str, end_date: str = "",
                    anchor_day: int | None = None, limit: int = 120) -> list[str]:
    """Every scheduled date from next_date up to and including `until` (and end_date)."""
    out = []
    d = next_date
    while d and d <= until and (not end_date or d <= end_date) and len(out) < limit:
        out.append(d)
        d = next_occurrence(d, freq, anchor_day)
    return out
