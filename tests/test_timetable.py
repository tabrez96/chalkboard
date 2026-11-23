from datetime import date, datetime, time

from chalkboard.timetable import (
    Budget,
    budget,
    class_slots,
    days_summary,
    format_budget,
    format_duration,
)

TEN = time(10, 0)


def test_s02_sample():
    times = {0: TEN, 2: TEN}
    first, last = date(2026, 11, 23), date(2027, 2, 26)
    slots = class_slots(times, first, last)
    assert len(slots) == 28
    assert slots[0] == datetime(2026, 11, 23, 10, 0)
    assert slots[-1] == datetime(2027, 2, 24, 10, 0)
    b = budget(times, first, last, 50)
    assert b == Budget(28, 1400)
    assert format_budget(b) == "28 classes · 23 h 20 min"


def test_no_timetable():
    assert class_slots({}, date(2026, 11, 23), date(2027, 2, 26)) == []
    assert budget({}, date(2026, 11, 23), date(2027, 2, 26), 50) is None
    assert budget({0: TEN}, None, None, 50) is None
    assert budget({0: TEN}, date(2026, 11, 23), None, 50) is None


def test_reversed_term_has_no_slots():
    assert class_slots({0: TEN}, date(2026, 12, 1), date(2026, 11, 1)) == []


def test_single_day():
    b = budget({1: time(11, 15)}, date(2026, 11, 24), date(2026, 12, 15), 45)
    assert b == Budget(4, 180)
    assert format_budget(b) == "4 classes · 3 h"


def test_one_day_term():
    monday = date(2026, 11, 23)
    assert format_budget(budget({0: TEN}, monday, monday, 50)) == "1 class · 50 min"
    assert (
        format_budget(budget({1: TEN}, monday, monday, 50)) == "No classes in this term"
    )


def test_term_ending_on_date_max():
    # date.max is a Friday; stepping past it would overflow.
    assert class_slots({4: TEN}, date.max, date.max) == [
        datetime.combine(date.max, TEN)
    ]
    assert class_slots({0: TEN}, date.max, date.max) == []


def test_mixed_times_sorted():
    slots = class_slots(
        {4: TEN, 1: time(11, 15)}, date(2026, 11, 23), date(2026, 11, 29)
    )
    assert slots == [datetime(2026, 11, 24, 11, 15), datetime(2026, 11, 27, 10, 0)]


def test_days_summary():
    assert days_summary({2: TEN, 0: TEN}) == "Mon, Wed 10:00"
    assert days_summary({4: TEN, 1: time(11, 15)}) == "Tue 11:15, Fri 10:00"
    assert days_summary({}) == ""


def test_format_duration():
    assert format_duration(1400) == "23 h 20 min"
    assert format_duration(120) == "2 h"
    assert format_duration(45) == "45 min"
    assert format_duration(0) == "0 min"
