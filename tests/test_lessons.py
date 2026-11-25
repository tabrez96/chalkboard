from decimal import Decimal

import pytest
from sqlalchemy import func, select

from chalkboard import plans
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.chapters import SPLIT_KIND, ChapterError, title_key
from chalkboard.jobs import JobContext
from chalkboard.lessons import (
    NO_STEPS,
    enqueue_split,
    lessons_for,
    parse_class_titles,
    parse_steps,
    split_minutes,
)
from chalkboard.models import Job, PlanVersion, Subject
from chalkboard.overview import STEP_CHECK
from tests.plan_fixtures import add_source
from tests.test_chapters import (
    PASSAGES,
    SUPPORTED,
    approve_overview,
    run_all,
    store_chapter,
)


@pytest.fixture
def server(app):
    fake = FakeOllama()
    app.extensions[FACTORY_KEY] = lambda config, redactor: RedactingClient(
        fake, redactor
    )
    return fake


@pytest.fixture
def subject(db_session, teacher):
    row = Subject(
        teacher_id=teacher.id,
        name="Physics",
        class_minutes=60,
        colour="#0E7A64",
        board="none",
        grade="Class 11",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def sourced(db_session, subject):
    add_source(db_session, subject, 1, PASSAGES)
    db_session.commit()
    return subject


def steps(*pairs, labels=("P1",)):
    return {
        "steps": [
            {"text": text, "minutes": m, "passages": list(labels)} for text, m in pairs
        ]
    }


def class_steps():
    return steps(("Warm-up", 10), ("Teach", 20), ("Practice", 20), ("Exit", 10))


def script(n):
    return [
        {"classes": [{"title": f"Class {i + 1}"} for i in range(n)]},
        *[class_steps() for _ in range(n)],
        *([SUPPORTED] * (4 * n)),
    ]


def approved_chapter(db_session, subject, hours):
    approve_overview(db_session, subject, ["Forces"], Decimal(hours))
    key = title_key("Forces")
    chapter = store_chapter(db_session, subject, key, "Forces")
    plans.approve(db_session, chapter)
    db_session.commit()
    return key, chapter


def split(app, db_session, subject, chapter):
    job = enqueue_split(db_session, subject, chapter)
    db_session.commit()
    run_all(app)
    db_session.expire_all()
    return job


def lessons_versions(db_session, subject):
    return list(
        db_session.scalars(
            select(PlanVersion)
            .where(PlanVersion.subject_id == subject.id, PlanVersion.kind == "lessons")
            .order_by(PlanVersion.number)
        )
    )


def class_minutes_of(version):
    return [
        sum(s.minutes for s in version.sentences if s.item_id == item.id)
        for item in version.items
    ]


def test_split_chapter_makes_three_classes(app, db_session, server, sourced):
    key, chapter = approved_chapter(db_session, sourced, "3.0")
    server.generated = script(3)
    job = split(app, db_session, sourced, chapter)
    assert job.state == "done"
    [lessons] = lessons_versions(db_session, sourced)
    assert (lessons.chapter_key, lessons.number) == (key, 1)
    assert lessons.parent_version_id == chapter.id
    assert [(i.kind, i.title) for i in lessons.items] == [
        ("lesson", "Class 1"),
        ("lesson", "Class 2"),
        ("lesson", "Class 3"),
    ]
    assert all(s.minutes for s in lessons.sentences)
    assert all(s.citations for s in lessons.sentences)
    assert class_minutes_of(lessons) == [60, 60, 60]
    assert lessons_for(db_session, [chapter.id])[chapter.id].id == lessons.id
    assert lessons_for(db_session, []) == {}


def test_check_heartbeats_each_class(app, db_session, server, sourced, monkeypatch):
    _, chapter = approved_chapter(db_session, sourced, "3.0")
    server.generated = script(3)
    calls = []
    original = JobContext.progress

    def record(self, step, percent):
        calls.append((step, percent))
        return original(self, step, percent)

    monkeypatch.setattr(JobContext, "progress", record)
    split(app, db_session, sourced, chapter)
    assert [p for step, p in calls if step == STEP_CHECK] == [75, 81, 88]


def test_remainder_class_is_shorter(app, db_session, server, sourced):
    _, chapter = approved_chapter(db_session, sourced, "2.5")
    server.generated = script(3)
    split(app, db_session, sourced, chapter)
    [lessons] = lessons_versions(db_session, sourced)
    assert class_minutes_of(lessons) == [60, 60, 30]


def test_uncited_steps_dropped_and_empty_class_fails(app, db_session, server, sourced):
    _, chapter = approved_chapter(db_session, sourced, "2.0")
    server.generated = [
        {"classes": [{"title": "A"}, {"title": "B"}]},
        class_steps(),
        steps(("No label", 30), labels=("P9",)),
    ]
    job = split(app, db_session, sourced, chapter)
    assert job.state == "failed"
    assert job.error == NO_STEPS.format(n=2)
    assert lessons_versions(db_session, sourced) == []


def test_job_needs_an_approved_chapter(app, db_session, server, sourced):
    approve_overview(db_session, sourced, ["Forces"], Decimal("1.0"))
    chapter = store_chapter(db_session, sourced, title_key("Forces"), "Forces")
    db_session.commit()
    job = split(app, db_session, sourced, chapter)
    assert job.state == "failed"
    assert lessons_versions(db_session, sourced) == []


def test_resplit_belongs_to_new_version(app, db_session, server, sourced):
    key, v1 = approved_chapter(db_session, sourced, "1.0")
    server.generated = script(1)
    split(app, db_session, sourced, v1)
    [first] = lessons_versions(db_session, sourced)
    before = [(s.id, s.text, s.minutes) for s in first.sentences]

    v2 = store_chapter(db_session, sourced, key, "Forces")
    plans.approve(db_session, v2)
    db_session.commit()
    server.generated = script(1)
    split(app, db_session, sourced, v2)

    first_again, second = lessons_versions(db_session, sourced)
    assert (first_again.number, second.number) == (1, 2)
    assert (first_again.parent_version_id, second.parent_version_id) == (v1.id, v2.id)
    assert before == [(s.id, s.text, s.minutes) for s in first_again.sentences]
    assert lessons_for(db_session, [v1.id, v2.id]).keys() == {v1.id, v2.id}


def test_enqueue_split_rejects_too_many_classes_and_dedupes(
    db_session, server, sourced
):
    _, chapter = approved_chapter(db_session, sourced, "1.0")
    first = enqueue_split(db_session, sourced, chapter)
    db_session.commit()
    assert enqueue_split(db_session, sourced, chapter).id == first.id
    assert first.payload == {
        "subject_id": sourced.id,
        "chapter_key": chapter.chapter_key,
        "chapter_version_id": chapter.id,
    }
    assert (
        db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == SPLIT_KIND)
        )
        == 1
    )

    approve_overview(db_session, sourced, ["Long"], Decimal("40.0"))
    long = store_chapter(db_session, sourced, title_key("Long"), "Long")
    db_session.commit()
    with pytest.raises(ChapterError, match="more than 30 classes"):
        enqueue_split(db_session, sourced, long)
    wrong_kind = db_session.scalars(
        select(PlanVersion).where(PlanVersion.kind == "overview")
    ).first()
    with pytest.raises(ChapterError):
        enqueue_split(db_session, sourced, wrong_kind)


def test_split_minutes():
    assert split_minutes(Decimal("2.5"), 60) == [60, 60, 30]
    assert split_minutes(None, 45) == [45]


def test_parse_steps_and_titles():
    labels = {"P1": 11, "P2": 22}
    answer = {
        "steps": [
            {"text": " Warm  up ", "minutes": 10, "passages": ["p1", "P1", "P2"]},
            {"text": "Bool minutes", "minutes": True, "passages": ["P2"]},
            {"text": "Str minutes", "minutes": "20", "passages": ["P1"]},
            {"text": "No label", "minutes": 5, "passages": ["P9"]},
            {"text": "", "minutes": 5, "passages": ["P1"]},
            {"text": "Bad passages", "minutes": 5, "passages": "P1"},
            "junk",
        ]
    }
    parsed = parse_steps(answer, labels)
    assert [(s.text, s.chunk_ids, s.minutes) for s in parsed] == [
        ("Warm up", (11, 22), 10),
        ("Bool minutes", (22,), 0),
        ("Str minutes", (11,), 0),
    ]
    assert parse_steps("nope", labels) == []

    assert parse_class_titles(
        {"classes": [{"title": " A  b "}, {"title": ""}]}, 3, "Ch"
    ) == [
        "A b",
        "Ch, part 2",
        "Ch, part 3",
    ]
    assert parse_class_titles({"classes": [{"title": "x"}] * 4}, 2, "Ch") == ["x", "x"]
    assert parse_class_titles(None, 1, "Ch") == ["Ch, part 1"]


def test_enqueue_split_for_new_version_while_old_split_is_active(
    db_session, server, sourced
):
    key, v1 = approved_chapter(db_session, sourced, "1.0")
    first = enqueue_split(db_session, sourced, v1)
    db_session.commit()
    v2 = store_chapter(db_session, sourced, key, "Forces")
    plans.approve(db_session, v2)
    second = enqueue_split(db_session, sourced, v2)
    db_session.commit()
    assert second.id != first.id
    assert second.payload["chapter_version_id"] == v2.id
    assert enqueue_split(db_session, sourced, v2).id == second.id


def test_enqueue_split_reuses_own_version_job_when_newer_exists(
    db_session, server, sourced
):
    key, v1 = approved_chapter(db_session, sourced, "1.0")
    first = enqueue_split(db_session, sourced, v1)
    db_session.commit()
    v2 = store_chapter(db_session, sourced, key, "Forces")
    plans.approve(db_session, v2)
    second = enqueue_split(db_session, sourced, v2)
    db_session.commit()
    jobs_before = db_session.scalar(select(func.count()).select_from(Job))
    assert enqueue_split(db_session, sourced, v1).id == first.id
    assert second.id != first.id
    assert db_session.scalar(select(func.count()).select_from(Job)) == jobs_before
