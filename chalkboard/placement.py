"""Lesson placement: every class of the course on the subject's class slots, in order. Pure: no Flask, no database."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class PlannedClass:
    chapter: int  # 1-based chapter number in the chapter list
    chapter_key: str
    index: int  # 1-based class number within the chapter
    of: int  # classes in the chapter
    item_id: int | None  # the lesson's PlanItem id; None until the chapter is split
    title: str  # the lesson title, or the chapter title before the split
    minutes: int


@dataclass(frozen=True)
class Placed:
    lesson: PlannedClass
    when: datetime | None  # None: after the term's last class


@dataclass(frozen=True)
class DayView:
    state: str  # "lesson" | "not_written" | "no_lesson" | "no_timetable" | "term_over"
    today: Placed | None = None  # the class on this day
    next: Placed | None = None  # no_lesson: the first placed class after this day
    not_taught: int = 0  # term_over: classes that fell after the last class
    last: datetime | None = None  # the term's last class slot


def place(classes: Sequence[PlannedClass], slots: Sequence[datetime]) -> list[Placed]:
    return [
        Placed(c, slots[i] if i < len(slots) else None) for i, c in enumerate(classes)
    ]


def wont_fit(placed: Sequence[Placed]) -> int | None:
    """The chapter of the first class that falls after the term's last class."""
    return next((p.lesson.chapter for p in placed if p.when is None), None)


def next_class(placed: Sequence[Placed], day: date) -> Placed | None:
    return next((p for p in placed if p.when and p.when.date() >= day), None)


def next_unfinished(placed: Sequence[Placed], moment: datetime) -> Placed | None:
    """The first class that has not ended at `moment`; a class under way still counts."""
    return next(
        (
            p
            for p in placed
            if p.when and p.when + timedelta(minutes=p.lesson.minutes) > moment
        ),
        None,
    )


def coming_up(placed: Sequence[Placed], day: date, days: int = 7) -> list[Placed]:
    end = day + timedelta(days=days)
    return [p for p in placed if p.when and day <= p.when.date() < end]


def lesson_neighbours(
    placed: Sequence[Placed], item_id: int
) -> tuple[Placed | None, Placed | None]:
    written = [p for p in placed if p.lesson.item_id is not None]
    for i, p in enumerate(written):
        if p.lesson.item_id == item_id:
            before = written[i - 1] if i > 0 else None
            after = written[i + 1] if i + 1 < len(written) else None
            return before, after
    return None, None


def day_view(placed: Sequence[Placed], slots: Sequence[datetime], day: date) -> DayView:
    if not slots:
        return DayView("no_timetable")
    if day > slots[-1].date():
        return DayView(
            "term_over",
            not_taught=sum(1 for p in placed if p.when is None),
            last=slots[-1],
        )
    p = next((p for p in placed if p.when and p.when.date() == day), None)
    if p is not None:
        return DayView("lesson" if p.lesson.item_id is not None else "not_written", p)
    if any(s.date() == day for s in slots):
        return DayView("not_written")
    return DayView("no_lesson", next=next_class(placed, day + timedelta(days=1)))


def short_when(d: datetime) -> str:
    return f"{d:%a} {d.day} {d:%b}, {d:%H:%M}"


def short_date(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def long_date(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B}"


def class_line(c: PlannedClass) -> str:
    return f"Chapter {c.chapter}, class {c.index} of {c.of}"


def time_range(start: datetime, minutes: int) -> str:
    return f"{start:%H:%M}–{start + timedelta(minutes=minutes):%H:%M}"


def pages_short(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    if end is None or end == start:
        return f"p. {start}"
    return f"p. {start}–{end}"
