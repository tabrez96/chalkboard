"""Class slots and the course budget from class days, start times and term dates. Pure: no Flask, no database."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


@dataclass(frozen=True)
class Budget:
    classes: int
    minutes: int


def class_slots(
    times: Mapping[int, time], first: date | None, last: date | None
) -> list[datetime]:
    """Every class in the term: each chosen weekday between first and last (inclusive) at its start time, in order."""
    if not times or first is None or last is None or last < first:
        return []
    slots = []
    day = first
    while True:
        if day.weekday() in times:
            slots.append(datetime.combine(day, times[day.weekday()]))
        if day == last:  # stop before stepping past date.max
            break
        day += timedelta(days=1)
    return slots


def budget(
    times: Mapping[int, time],
    first: date | None,
    last: date | None,
    class_minutes: int,
) -> Budget | None:
    """None when there is no timetable: no class times, or no term."""
    if not times or first is None or last is None:
        return None
    n = len(class_slots(times, first, last))
    return Budget(n, n * class_minutes)


def format_duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def format_budget(b: Budget) -> str:
    if b.classes == 0:
        return "No classes in this term"
    noun = "class" if b.classes == 1 else "classes"
    return f"{b.classes} {noun} · {format_duration(b.minutes)}"


def days_summary(times: Mapping[int, time]) -> str:
    if not times:
        return ""
    days = sorted(times)
    if len({times[d] for d in days}) == 1:
        return f"{', '.join(WEEKDAYS[d] for d in days)} {times[days[0]]:%H:%M}"
    return ", ".join(f"{WEEKDAYS[d]} {times[d]:%H:%M}" for d in days)
