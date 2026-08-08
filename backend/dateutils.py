"""
Small date helpers — deliberately dependency-free (no python-dateutil)
to keep the requirements list short.
"""

from __future__ import annotations

import datetime
import calendar


def add_months(start: datetime.date, months: int) -> datetime.date:
    """Adds N calendar months to a date, clamping the day if the target
    month is shorter (e.g. 31 Jan + 1 month -> 28/29 Feb)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def parse_start_date(value: str) -> datetime.date | None:
    """Parses the dd-mm-yyyy format used in extracted_fields/templates.
    Returns None if it can't be parsed — callers should handle that by
    leaving agreement_end_date blank for staff to fill in manually."""
    if not value:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def calculate_agreement_end_date(fields: dict) -> datetime.date | None:
    start = parse_start_date(fields.get("start_date", ""))
    if not start:
        return None
    try:
        months = int(fields.get("agreement_duration_months", 0))
    except (ValueError, TypeError):
        return None
    if months <= 0:
        return None
    return add_months(start, months)
