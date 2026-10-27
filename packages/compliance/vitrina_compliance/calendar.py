"""The legal calendar for marketing messages (Ley 2300 de 2023; Art. II.2; FR-062; ADR-0008).

Monday to Friday 07:00–19:00 and Saturday 08:00–15:00, America/Bogota; never on Sundays or
Colombian public holidays. Each window ends before its closing hour: 19:00 itself is out.
Holidays come from the pinned ``holidays`` library (country CO, which applies the Emiliani
rule of Ley 51 de 1983), for any year asked.
"""

from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import holidays

BOGOTA = ZoneInfo("America/Bogota")
WINDOWS = {  # weekday() → (opens, closes); Sunday has none
    **dict.fromkeys(range(5), (time(7), time(19))),
    5: (time(8), time(15)),
}
SEARCH_DAYS = 14  # Colombia never has two weeks without a window


@cache
def _holidays(year: int) -> holidays.HolidayBase:
    return holidays.country_holidays("CO", years=year)


def is_holiday(day: date) -> bool:
    return day in _holidays(day.year)


def window(day: date) -> tuple[datetime, datetime] | None:
    """The day's legal window in Bogotá, or None on Sundays and holidays."""
    hours = WINDOWS.get(day.weekday())
    if hours is None or is_holiday(day):
        return None
    opens, closes = hours
    return datetime.combine(day, opens, BOGOTA), datetime.combine(day, closes, BOGOTA)


def _local(at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("a send time needs its time zone; naive times are refused, not guessed")
    return at.astimezone(BOGOTA)


def is_legal(at: datetime) -> bool:
    local = _local(at)
    span = window(local.date())
    return span is not None and span[0] <= local < span[1]


def next_legal(at: datetime) -> datetime:
    """``at`` itself when legal, else the opening of the next window (in Bogotá time)."""
    local = _local(at)
    for offset in range(SEARCH_DAYS):
        span = window(local.date() + timedelta(days=offset))
        if span is None:
            continue
        opens, closes = span
        if offset == 0 and local >= closes:
            continue
        return max(opens, local) if offset == 0 else opens
    raise AssertionError(f"no legal window within {SEARCH_DAYS} days of {at.isoformat()}")
