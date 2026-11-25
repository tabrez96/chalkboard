from datetime import date, datetime, time

import pytest
from sqlalchemy import delete, select

from chalkboard.models import Citation, ClassTime, Sentence, Subject, SyllabusUnit
from tests.conftest import sign_in
from tests.plan_fixtures import add_source
from tests.test_chapters_page import (
    PASSAGES,
    approve_overview,
    approved_with_lessons,
    html,
)


@pytest.fixture
def subject(db_session, teacher):
    row = Subject(
        teacher_id=teacher.id,
        name="Physics",
        class_minutes=60,
        colour="#0E7A64",
        board="cbse",
        grade="Class 11",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def sourced(db_session, subject):
    syllabus = add_source(db_session, subject, 1, ["Unit I Motion"], is_syllabus=True)
    db_session.add_all(
        SyllabusUnit(source_id=syllabus[0].source_id, position=i, number=i + 1, title=t)
        for i, t in enumerate(["Motion", "Energy"])
    )
    add_source(db_session, subject, 2, PASSAGES)
    db_session.commit()
    return subject


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


@pytest.fixture
def timetable(db_session, sourced):
    sourced.class_times = [
        ClassTime(weekday=1, start=time(9)),
        ClassTime(weekday=3, start=time(9)),
    ]
    sourced.term_start = date(2026, 11, 24)
    sourced.term_end = date(2026, 11, 26)
    db_session.commit()
    return sourced


def at(monkeypatch, *moment):
    fixed = datetime(*moment)
    monkeypatch.setattr("chalkboard.today_page.now", lambda: fixed)
    monkeypatch.setattr("chalkboard.subjects.now", lambda: fixed)


def today_url(subject, tail=""):
    return f"/subjects/{subject.id}/today{tail}"


def test_today_shows_placed_lesson(signed_in, db_session, timetable, monkeypatch):
    _, lessons = approved_with_lessons(db_session, timetable)
    at(monkeypatch, 2026, 11, 24, 8)
    page = html(signed_in, today_url(timetable))
    for text in (
        "Physics · Tuesday 24 November · Chapter 1, class 1 of 3",
        "Class title 1",
        "09:00–10:00",
        'class="cite"',
        "Sources",
    ):
        assert text in page
    second = lessons.items[1].id
    assert (
        f'href="{today_url(timetable, f"/lessons/{second}")}" aria-label="Next class"'
        in page
    )
    assert 'aria-disabled="true" aria-label="Previous class"' in page


def test_no_lesson_today_offers_next(signed_in, db_session, timetable, monkeypatch):
    _, lessons = approved_with_lessons(db_session, timetable)
    at(monkeypatch, 2026, 11, 25, 8)
    page = html(signed_in, today_url(timetable))
    assert "No Physics class today" in page
    assert "Thursday 26 November, 09:00" in page
    second = lessons.items[1].id
    assert today_url(timetable, f"/lessons/{second}") in page
    assert "Open Thursday's lesson" in page


def test_no_timetable_state(signed_in, db_session, timetable, monkeypatch):
    db_session.execute(delete(ClassTime))
    db_session.commit()
    at(monkeypatch, 2026, 11, 24, 8)
    page = html(signed_in, today_url(timetable))
    assert "Physics has no timetable yet" in page
    assert f'href="/subjects/{timetable.id}/settings">Add class times' in page


def test_term_over_state(signed_in, db_session, timetable, monkeypatch):
    approved_with_lessons(db_session, timetable)
    at(monkeypatch, 2026, 11, 27, 8)
    page = html(signed_in, today_url(timetable))
    assert "The Physics term is over" in page
    assert "1 lesson wasn't taught" in page


def test_not_written_state(signed_in, db_session, timetable, monkeypatch):
    approve_overview(db_session, timetable, ["Forces"])
    at(monkeypatch, 2026, 11, 24, 8)
    page = html(signed_in, today_url(timetable))
    assert "isn't written yet" in page


def test_lesson_url_and_passage(signed_in, db_session, timetable):
    _, lessons = approved_with_lessons(db_session, timetable)
    first, _, third = (i.id for i in lessons.items)
    page = html(signed_in, today_url(timetable, f"/lessons/{third}"))
    assert "No class date" in page

    def cite_of(item_id, same=True):
        return db_session.scalars(
            select(Citation.id)
            .join(Sentence, Sentence.id == Citation.sentence_id)
            .where(Sentence.item_id == item_id if same else Sentence.item_id != item_id)
            .limit(1)
        ).one()

    path = today_url(timetable, f"/lessons/{first}/citations/{cite_of(first)}")
    assert "A force changes the motion" in html(signed_in, path)
    other = cite_of(first, same=False)
    path = today_url(timetable, f"/lessons/{first}/citations/{other}")
    assert signed_in.get(path).status_code == 404
    assert signed_in.get(today_url(timetable, "/lessons/999999")).status_code == 404


def test_subjects_card_and_coming_up(signed_in, db_session, timetable, monkeypatch):
    approved_with_lessons(db_session, timetable)
    at(monkeypatch, 2026, 11, 24, 8)
    page = html(signed_in, "/subjects")
    assert "Tue 24 Nov, 09:00" in page and "Class title 1" in page
    assert "Chapter 1 won't fit the term" in page
    assert "Thu 26 Nov, 09:00" in page and "Ready" in page


def test_card_skips_finished_class(signed_in, db_session, timetable, monkeypatch):
    approved_with_lessons(db_session, timetable)
    at(monkeypatch, 2026, 11, 24, 10, 1)
    page = html(signed_in, "/subjects")
    assert "Next class" in page
    assert "<strong>Thu 26 Nov, 09:00</strong>" in page
    assert "<strong>Tue 24 Nov, 09:00</strong>" not in page


def test_card_has_no_wont_fit_chip_without_timetable(
    signed_in, db_session, timetable, monkeypatch
):
    approved_with_lessons(db_session, timetable)
    db_session.execute(delete(ClassTime))
    db_session.commit()
    at(monkeypatch, 2026, 11, 24, 8)
    assert "won't fit the term" not in html(signed_in, "/subjects")


def test_full_page_ignores_other_lessons_citation(signed_in, db_session, timetable):
    _, lessons = approved_with_lessons(db_session, timetable)
    first, second = lessons.items[0].id, lessons.items[1].id
    own = db_session.scalars(
        select(Citation.id)
        .join(Sentence, Sentence.id == Citation.sentence_id)
        .where(Sentence.item_id == first)
        .limit(1)
    ).one()
    other = db_session.scalars(
        select(Citation.id)
        .join(Sentence, Sentence.id == Citation.sentence_id)
        .where(Sentence.item_id == second)
        .limit(1)
    ).one()
    base = today_url(timetable, f"/lessons/{first}")
    assert "passage-quote" in html(signed_in, f"{base}?cite={own}")
    assert "passage-quote" not in html(signed_in, f"{base}?cite={other}")
