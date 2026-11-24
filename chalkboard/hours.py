"""Chapter hours: class counts, fitting chapters to the term's classes, and the course budget summary. Pure: no Flask, no database."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from chalkboard.timetable import class_slots, format_duration

MAX_TENTHS = 9999  # PlanItem.hours_estimate is Numeric(4, 1): at most 999.9 h
WONT_FIT = " won't fit. Ask for shorter chapters with Request changes, or move the term's last day in subject settings."


def class_count(hours: Decimal | None, class_minutes: int) -> int | None:
    """Classes a chapter takes: hours x 60 / class length, rounded up. None when the hours are unknown."""
    if hours is None:
        return None
    tenths = int(hours * 10)
    return -(-tenths * 6 // class_minutes)


def hours_for(classes: int, class_minutes: int) -> Decimal:
    """Hours for a number of classes, rounded down to 0.1 h so that class_count gives the same number back."""
    tenths = min(classes * class_minutes // 6, MAX_TENTHS)
    return Decimal(tenths) / 10


def fit_classes(
    weights: Sequence[float], class_minutes: int, term_classes: int | None
) -> list[int]:
    """Classes per chapter from the model's hour estimates, using up exactly the term's classes when there are enough."""
    n = len(weights)
    if n == 0:
        return []
    if term_classes is None:
        return [max(1, math.ceil(w * 60 / class_minutes)) for w in weights]
    if term_classes < n:
        return [1] * n
    extra = term_classes - n
    total = sum(weights)
    quotas = [extra * w / total for w in weights]
    counts = [1 + math.floor(q) for q in quotas]
    leftover = term_classes - sum(counts)
    by_remainder = sorted(
        range(n), key=lambda i: (-(quotas[i] - math.floor(quotas[i])), i)
    )
    for i in by_remainder[:leftover]:
        counts[i] += 1
    return counts


@dataclass(frozen=True)
class Spill:
    chapter: int  # 0-based index of the chapter
    classes: int  # its classes that fall after the term ends
    label: str  # "2 classes after the term ends" / "1 class after the term ends"


@dataclass(frozen=True)
class BudgetSummary:
    planned_minutes: int
    budget_minutes: int
    chip: str  # "14 h of 16 h planned"
    free: str | None  # "2 h free" when under budget, else None
    over: str | None  # "2 h" when over budget, else None
    spills: tuple[Spill, ...]
    alert_title: str | None  # "2 hours more than the term has."
    alert_body: str | None

    @property
    def is_over(self) -> bool:
        return self.planned_minutes > self.budget_minutes


def spoken_duration(minutes: int) -> str:
    """'2 hours', '1 hour', '45 minutes', '1 hour 30 minutes'."""
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'' if hours == 1 else 's'}")
    if rest or not hours:
        parts.append(f"{rest} minute{'' if rest == 1 else 's'}")
    return " ".join(parts)


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def summarise(
    hours: Sequence[Decimal | None],
    class_minutes: int,
    times: Mapping[int, time],
    first: date | None,
    last: date | None,
) -> BudgetSummary | None:
    """The course budget against the planned chapters; None when there is no timetable."""
    if not times or first is None or last is None:
        return None
    slots = class_slots(times, first, last)
    term_classes = len(slots)
    budget_minutes = term_classes * class_minutes
    counts = [class_count(h, class_minutes) or 0 for h in hours]
    planned_minutes = sum(counts) * class_minutes
    chip = f"{format_duration(planned_minutes)} of {format_duration(budget_minutes)} planned"
    if planned_minutes <= budget_minutes:
        free = (
            f"{format_duration(budget_minutes - planned_minutes)} free"
            if planned_minutes < budget_minutes
            else None
        )
        return BudgetSummary(
            planned_minutes, budget_minutes, chip, free, None, (), None, None
        )
    excess = planned_minutes - budget_minutes
    spills = []
    parts = []
    start = 0
    for i, c in enumerate(counts):
        after = min(c, max(0, start + c - term_classes))
        start += c
        if after == 0:
            continue
        noun = "class" if after == 1 else "classes"
        spills.append(Spill(i, after, f"{after} {noun} after the term ends"))
        if after == c:
            parts.append(f"all of Chapter {i + 1}")
        elif after == 1:
            parts.append(f"the last class of Chapter {i + 1}")
        else:
            parts.append(f"the last {after} classes of Chapter {i + 1}")
    if slots:
        d = slots[-1]
        lead = f"The last class is {d:%a} {d.day} {d:%b}, so "
    else:
        lead = "The term has no classes, so "
    return BudgetSummary(
        planned_minutes,
        budget_minutes,
        chip,
        None,
        format_duration(excess),
        tuple(spills),
        f"{spoken_duration(excess)} more than the term has.",
        f"{lead}{_join(parts)}{WONT_FIT}",
    )
