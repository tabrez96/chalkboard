from datetime import date, time
from decimal import Decimal

from chalkboard.hours import (
    class_count,
    fit_classes,
    hours_for,
    spoken_duration,
    summarise,
)

TUE_THU = {1: time(9), 3: time(9)}
# Thu 19 Nov 2026 to Tue 12 Jan 2027 holds exactly 16 Tuesday/Thursday classes.
FIRST, LAST = date(2026, 11, 19), date(2027, 1, 12)


def test_class_count_rounds_up():
    assert class_count(Decimal("1.5"), 60) == 2
    assert class_count(Decimal("2.2"), 45) == 3
    assert class_count(None, 60) is None
    assert class_count(Decimal(0), 60) == 0


def test_hours_for_round_trips():
    for m in range(10, 241):
        for c in range(61):
            assert class_count(hours_for(c, m), m) == c


def test_hours_for_is_capped_at_the_column_limit():
    assert hours_for(10_000, 60) == Decimal("999.9")


def test_fit_sums_to_term_classes():
    counts = fit_classes([3, 1, 2], 60, 12)
    assert sum(counts) == 12
    assert min(counts) >= 1
    assert counts[0] > counts[2] > counts[1]


def test_fit_ties_go_to_earlier_chapter():
    assert fit_classes([1, 1, 1], 60, 4) == [2, 1, 1]


def test_fit_term_shorter_than_chapters():
    assert fit_classes([1, 1, 1], 60, 2) == [1, 1, 1]
    assert fit_classes([1, 1, 1], 60, 0) == [1, 1, 1]


def test_fit_without_timetable():
    assert fit_classes([1.5, 0.2], 60, None) == [2, 1]


def test_fit_nothing():
    assert fit_classes([], 60, 10) == []


def hours(*values):
    return [Decimal(v) for v in values]


def test_summary_none_without_timetable():
    assert summarise(hours(3), 60, {}, FIRST, LAST) is None
    assert summarise(hours(3), 60, TUE_THU, None, None) is None


def test_summary_under_budget():
    s = summarise(hours(3, 7, 4), 60, TUE_THU, FIRST, LAST)
    assert s.chip == "14 h of 16 h planned"
    assert s.free == "2 h free"
    assert s.over is None
    assert s.spills == ()
    assert s.alert_title is None and s.alert_body is None
    assert not s.is_over


def test_summary_exact():
    s = summarise(hours(8, 8), 60, TUE_THU, FIRST, LAST)
    assert s.chip == "16 h of 16 h planned"
    assert s.free is None and s.over is None
    assert not s.is_over


def test_summary_over_budget_names_the_spill():
    s = summarise(hours(3, 7, 5, 3), 60, TUE_THU, FIRST, LAST)
    assert s.is_over
    assert s.chip == "18 h of 16 h planned"
    assert s.over == "2 h"
    assert s.free is None
    assert [(x.chapter, x.classes, x.label) for x in s.spills] == [
        (3, 2, "2 classes after the term ends")
    ]
    assert s.alert_title == "2 hours more than the term has."
    assert s.alert_body.startswith(
        "The last class is Tue 12 Jan, so the last 2 classes of Chapter 4 won't fit."
    )


def test_summary_spill_across_chapters():
    s = summarise(hours(10, 7, 3), 60, TUE_THU, FIRST, LAST)
    assert [(x.chapter, x.classes, x.label) for x in s.spills] == [
        (1, 1, "1 class after the term ends"),
        (2, 3, "3 classes after the term ends"),
    ]
    assert "the last class of Chapter 2 and all of Chapter 3 won't fit." in s.alert_body


def test_summary_three_parts_are_listed_with_commas():
    s = summarise(hours(1, 1, 1, 1), 60, TUE_THU, FIRST, FIRST)  # one class
    assert "all of Chapter 2, all of Chapter 3 and all of Chapter 4 won't fit." in (
        s.alert_body
    )


def test_summary_term_without_classes():
    s = summarise(hours(2), 60, TUE_THU, date(2026, 11, 20), date(2026, 11, 20))
    assert s.alert_body.startswith("The term has no classes, so all of Chapter 1")
    assert s.budget_minutes == 0


def test_spoken_duration():
    assert spoken_duration(120) == "2 hours"
    assert spoken_duration(60) == "1 hour"
    assert spoken_duration(45) == "45 minutes"
    assert spoken_duration(90) == "1 hour 30 minutes"
