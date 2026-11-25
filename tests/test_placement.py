from datetime import date, datetime, time

from chalkboard.placement import (
    PlannedClass,
    class_line,
    coming_up,
    day_view,
    lesson_neighbours,
    long_date,
    next_class,
    next_unfinished,
    pages_short,
    place,
    short_when,
    time_range,
    wont_fit,
)
from chalkboard.timetable import class_slots

TUE_THU = {1: time(9), 3: time(9)}
TUE = date(2026, 11, 24)


def classes(n, chapter=1, item_ids=None, start=1):
    return [
        PlannedClass(
            chapter,
            f"c{chapter}",
            start + i,
            n,
            item_ids[i] if item_ids else 100 * chapter + i,
            f"Class {chapter}.{start + i}",
            60,
        )
        for i in range(n)
    ]


def slots(last=date(2026, 12, 3)):
    return class_slots(TUE_THU, TUE, last)


def test_lessons_fill_class_days_in_order():
    placed = place(classes(3), slots())
    assert [p.when for p in placed] == [
        datetime(2026, 11, 24, 9),
        datetime(2026, 11, 26, 9),
        datetime(2026, 12, 1, 9),
    ]


def test_lessons_beyond_term_do_not_fit():
    placed = place(classes(2) + classes(1, chapter=2), slots(date(2026, 11, 26)))
    assert placed[2].when is None
    assert wont_fit(placed) == 2


def test_everything_fits_has_no_wont_fit():
    assert wont_fit(place(classes(2), slots())) is None


def test_placeholders_hold_slots():
    held = classes(2, item_ids=[None, None])
    placed = place(held + classes(1, chapter=2), slots())
    assert placed[1].when == datetime(2026, 11, 26, 9)
    assert placed[2].when == datetime(2026, 12, 1, 9)


def test_day_view_states():
    written = place(classes(2), slots(date(2026, 11, 26)))
    assert day_view(written, slots(date(2026, 11, 26)), TUE).state == "lesson"
    assert day_view(written, slots(date(2026, 11, 26)), TUE).today is written[0]

    held = place(classes(1, item_ids=[None]), slots(date(2026, 11, 26)))
    view = day_view(held, slots(date(2026, 11, 26)), TUE)
    assert (view.state, view.today) == ("not_written", held[0])

    empty = day_view([], slots(date(2026, 11, 26)), TUE)
    assert (empty.state, empty.today) == ("not_written", None)

    wed = day_view(written, slots(date(2026, 11, 26)), date(2026, 11, 25))
    assert (wed.state, wed.next) == ("no_lesson", written[1])

    assert day_view(written, [], TUE).state == "no_timetable"

    over = place(classes(3), slots(date(2026, 11, 26)))
    done = day_view(over, slots(date(2026, 11, 26)), date(2026, 11, 27))
    assert (done.state, done.not_taught) == ("term_over", 1)
    assert done.last == datetime(2026, 11, 26, 9)


def test_next_class_skips_days_before():
    placed = place(classes(3), slots())
    assert next_class(placed, date(2026, 11, 25)) is placed[1]
    assert next_class(placed, date(2026, 12, 2)) is None


def test_coming_up_is_seven_days():
    placed = place(
        classes(8), class_slots({i: time(9) for i in range(6)}, TUE, date(2026, 12, 31))
    )
    days = [p.when.date() for p in coming_up(placed, TUE)]
    assert TUE in days and date(2026, 11, 30) in days
    assert date(2026, 12, 1) not in days


def test_lesson_neighbours_skip_placeholders():
    held = classes(1, item_ids=[None])
    placed = place(held + classes(2) + classes(1, chapter=2), slots())
    first, second, third = (placed[i].lesson.item_id for i in (1, 2, 3))
    assert lesson_neighbours(placed, first) == (None, placed[2])
    assert lesson_neighbours(placed, second) == (placed[1], placed[3])
    assert lesson_neighbours(placed, third) == (placed[2], None)
    assert lesson_neighbours(placed, 999) == (None, None)


def test_format_helpers():
    when = datetime(2026, 11, 24, 9, 0)
    assert short_when(when) == "Tue 24 Nov, 09:00"
    assert long_date(when.date()) == "Tuesday 24 November"
    assert class_line(classes(3)[1]) == "Chapter 1, class 2 of 3"
    assert time_range(when, 60) == "09:00–10:00"
    assert pages_short(None, None) == ""
    assert pages_short(8, None) == "p. 8"
    assert pages_short(8, 8) == "p. 8"
    assert pages_short(8, 11) == "p. 8–11"


def test_next_unfinished_skips_ended_classes():
    placed = place(classes(3), slots())
    assert next_unfinished(placed, datetime(2026, 11, 24, 9, 59)) is placed[0]
    assert next_unfinished(placed, datetime(2026, 11, 24, 10, 0)) is placed[1]
    assert next_unfinished(placed, datetime(2026, 12, 3, 10, 0)) is None
