from datetime import date, time

import pytest
from sqlalchemy import select

from chalkboard import jobs, overview
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.db import get_session
from chalkboard.hours import class_count
from chalkboard.models import (
    Chunk,
    ClassTime,
    Job,
    PlanVersion,
    Source,
    Subject,
    SyllabusUnit,
    Teacher,
)
from chalkboard.overview import (
    NO_SOURCES,
    OverviewError,
    enqueue_overview,
    generate_overview,
    parse_outline,
)
from tests.plan_fixtures import add_source

PASSAGES = [
    "A force changes the motion of an object.",
    "Velocity is the rate of change of position.",
    "Energy is conserved in an isolated system.",
]


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
        board="cbse",
        grade="Class 11",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def sourced(db_session, subject):
    """A syllabus with two units (source 1) and a teaching source (source 2)."""
    syllabus = add_source(db_session, subject, 1, ["Unit I Motion"], is_syllabus=True)
    source_id = syllabus[0].source_id
    db_session.add_all(
        SyllabusUnit(source_id=source_id, position=i, number=i + 1, title=t)
        for i, t in enumerate(["Motion", "Energy"])
    )
    add_source(db_session, subject, 2, PASSAGES)
    db_session.commit()
    return subject


def run_all(app):
    while jobs.run_once(app):
        pass


def outline(*hours, units=(1, 2)):
    return {
        "chapters": [
            {"title": f"Chapter {i + 1}", "unit": units[i], "hours": h}
            for i, h in enumerate(hours)
        ]
    }


def written(chapter):
    return {
        "sentences": [
            {"text": f"Chapter {chapter} says {k}.", "passages": ["P1"]} for k in (1, 2)
        ]
    }


SUPPORTED = {"supported": True, "reason": "It says so"}


def script(*hours):
    """Outline, one write answer per chapter, then a supported check per sentence."""
    n = len(hours)
    return [
        outline(*hours),
        *(written(i + 1) for i in range(n)),
        *([SUPPORTED] * (2 * n)),
    ]


def run_overview(app, db_session, subject, parent=None, change=""):
    job = enqueue_overview(db_session, subject, parent, change)
    db_session.commit()
    run_all(app)
    db_session.expire_all()
    return job


def versions(db_session, subject):
    return sorted(
        db_session.scalars(
            select(PlanVersion).where(PlanVersion.subject_id == subject.id)
        ),
        key=lambda v: v.number,
    )


def snapshot(version):
    return (
        [(i.title, i.hours_estimate, i.unit_number) for i in version.items],
        [s.text for s in version.sentences],
    )


def timetable(db_session, subject, end=date(2026, 12, 29)):
    """Tuesdays and Thursdays from Thu 19 Nov 2026: 12 classes up to 29 Dec."""
    subject.class_times = [
        ClassTime(weekday=1, start=time(9)),
        ClassTime(weekday=3, start=time(9)),
    ]
    subject.term_start, subject.term_end = date(2026, 11, 19), end
    db_session.commit()


def unit(position, number, title="T"):
    return SyllabusUnit(position=position, number=number, title=title)


def test_parse_outline_cleans_and_orders_by_unit():
    units = [unit(0, 1), unit(1, 2), unit(2, 3)]
    answer = {
        "chapters": [
            {"title": "B", "unit": 2, "hours": 2},
            {"title": "   ", "unit": 1},
            "not a chapter",
            {"title": "A  of\n  it", "unit": 1, "hours": -3},
            {"title": "Floating", "unit": 99, "hours": True},
            {"title": "C", "unit": 3, "hours": "5"},
        ]
    }
    assert parse_outline(answer, units) == [
        overview.OutlineChapter("A of it", 1, 1.0),
        overview.OutlineChapter("Floating", None, 1.0),
        overview.OutlineChapter("B", 2, 2.0),
        overview.OutlineChapter("C", 3, 1.0),
    ]


def test_parse_outline_without_units_has_no_unit_numbers():
    answer = {"chapters": [{"title": "A", "unit": 1, "hours": 500}, {"title": "B"}]}
    chapters = parse_outline(answer, [])
    assert [(c.title, c.unit_number, c.weight) for c in chapters] == [
        ("A", None, 1.0),
        ("B", None, 1.0),
    ]


def test_parse_outline_rejects_other_shapes():
    assert parse_outline("nope", []) == []
    assert parse_outline({"chapters": "x"}, []) == []


def test_parse_outline_keeps_the_first_chapters():
    answer = {"chapters": [{"title": f"C{i}"} for i in range(40)]}
    assert len(parse_outline(answer, [])) == overview.MAX_CHAPTERS


def test_job_stores_version_one(app, db_session, server, sourced):
    server.generated = script(3, 1)
    job = run_overview(app, db_session, sourced)
    assert job.state == "done"
    [version] = versions(db_session, sourced)
    assert (version.number, version.kind) == (1, "overview")
    assert [(i.title, i.unit_number) for i in version.items] == [
        ("Chapter 1", 1),
        ("Chapter 2", 2),
    ]
    assert all(i.hours_estimate is not None for i in version.items)
    assert len(version.sentences) == 4
    for sentence in version.sentences:
        assert sentence.citations
        for citation in sentence.citations:
            source = db_session.scalar(
                select(Source)
                .join(Chunk, Chunk.source_id == Source.id)
                .where(Chunk.id == citation.chunk_id)
            )
            assert source.subject_id == sourced.id and not source.is_syllabus


def test_every_sentence_checked_and_flags_stored(app, db_session, server, sourced):
    answers = script(2, 2)
    answers[3] = {"supported": False, "reason": "Says something else"}
    server.generated = answers
    run_overview(app, db_session, sourced)
    [version] = versions(db_session, sourced)
    flagged = [s for s in version.sentences if s.check_flag]
    assert [s.text for s in flagged] == ["Chapter 1 says 1."]
    assert "Says something else" in flagged[0].check_reason
    assert all(s.check_reason is None for s in version.sentences if not s.check_flag)
    cited = sum(len(s.citations) for s in version.sentences)
    checks = [c for c in server.calls if "Does the passage support" in c[0]]
    assert cited == 4
    assert len(checks) == cited


def test_hours_fit_term_classes(app, db_session, server, sourced):
    timetable(db_session, sourced)
    server.generated = script(3, 1)
    run_overview(app, db_session, sourced)
    [version] = versions(db_session, sourced)
    counts = [class_count(i.hours_estimate, 60) for i in version.items]
    assert counts == [9, 3]
    assert sum(counts) == 12


def test_change_request_writes_next_version(app, db_session, server, sourced):
    server.generated = script(3, 1)
    run_overview(app, db_session, sourced)
    [v1] = versions(db_session, sourced)
    before = snapshot(v1)
    server.generated = script(2, 2)
    run_overview(app, db_session, sourced, v1, "Make chapter 2 shorter")
    v1, v2 = versions(db_session, sourced)
    assert v2.number == 2
    assert v2.parent_version_id == v1.id
    assert v2.change_request == "Make chapter 2 shorter"
    assert any("Make chapter 2 shorter" in call[0] for call in server.calls)
    assert any("1. Chapter 1 (" in call[0] for call in server.calls)
    assert snapshot(v1) == before
    assert v1.change_request == ""


def test_change_request_with_braces(app, db_session, server, sourced):
    request = "Use {x} and a lone { brace"
    server.generated = script(3, 1)
    run_overview(app, db_session, sourced)
    [v1] = versions(db_session, sourced)
    server.generated = script(2, 2)
    run_overview(app, db_session, sourced, v1, request)
    v1, v2 = versions(db_session, sourced)
    assert v2.number == 2
    assert v2.change_request == request
    assert any(request in call[0] for call in server.calls)


def test_progress_steps(app, db_session, server, sourced, monkeypatch):
    steps = []
    original = jobs.JobContext.progress

    def record(self, step, percent):
        steps.append(step)
        return original(self, step, percent)

    monkeypatch.setattr(jobs.JobContext, "progress", record)
    server.generated = script(1, 1)
    run_overview(app, db_session, sourced)
    assert steps == [
        "Finding passages",
        "Writing chapter 1 of 2",
        "Writing chapter 2 of 2",
        "Checking every sentence",
    ]


def test_unreachable_server_parks_and_stores_nothing(app, db_session, server, sourced):
    server.up = False
    job = run_overview(app, db_session, sourced)
    assert job.state == "waiting_ollama"
    assert versions(db_session, sourced) == []


def test_server_lost_mid_job_parks_and_stores_nothing(app, db_session, sourced):
    class Dropping(FakeOllama):
        def generate(self, prompt, schema):
            answer = super().generate(prompt, schema)
            if len(self.calls) >= 2:
                self.up = False
            return answer

    fake = Dropping(generated=script(1, 1))
    app.extensions[FACTORY_KEY] = lambda config, redactor: RedactingClient(
        fake, redactor
    )
    job = run_overview(app, db_session, sourced)
    assert job.state == "waiting_ollama"
    assert versions(db_session, sourced) == []


def test_no_sources_fails(app, db_session, server, subject):
    add_source(db_session, subject, 1, ["Unit I Motion"], is_syllabus=True)
    db_session.commit()
    job = run_overview(app, db_session, subject)
    assert (job.state, job.error) == ("failed", NO_SOURCES)
    assert versions(db_session, subject) == []


def test_no_outline_fails_after_retries(app, db_session, server, sourced):
    job = run_overview(app, db_session, sourced)  # the fake answers {}
    assert job.state in ("queued", "failed")
    assert job.error == overview.NO_OUTLINE
    assert versions(db_session, sourced) == []


def test_enqueue_returns_active_job(app, db_session, server, sourced):
    first = enqueue_overview(db_session, sourced)
    db_session.commit()
    assert enqueue_overview(db_session, sourced).id == first.id
    assert overview.active_overview_job(db_session, sourced.id).id == first.id
    server.generated = script(1, 1)
    run_all(app)
    db_session.expire_all()
    assert first.state == "done"
    assert overview.active_overview_job(db_session, sourced.id) is None
    third = enqueue_overview(db_session, sourced)
    assert third.id != first.id
    assert overview.latest_overview_job(db_session, sourced.id).id == third.id


def test_enqueue_rejects_blank_change_and_foreign_parent(
    app, db_session, server, sourced, teacher
):
    server.generated = script(1, 1)
    run_overview(app, db_session, sourced)
    [v1] = versions(db_session, sourced)
    with pytest.raises(OverviewError):
        enqueue_overview(db_session, sourced, v1, "   ")
    with pytest.raises(OverviewError):
        enqueue_overview(db_session, sourced, v1, "x" * (overview.CHANGE_MAX + 1))
    other = Subject(
        teacher_id=teacher.id, name="Maths", class_minutes=60, colour="#0E7A64"
    )
    db_session.add(other)
    db_session.commit()
    with pytest.raises(OverviewError):
        enqueue_overview(db_session, other, v1, "Shorter")


def test_lookups_return_newest_first(app, db_session, server, sourced):
    server.generated = script(1, 1)
    run_overview(app, db_session, sourced)
    [v1] = versions(db_session, sourced)
    server.generated = script(1, 1)
    run_overview(app, db_session, sourced, v1, "Again")
    assert [v.number for v in overview.overview_versions(db_session, sourced.id)] == [
        2,
        1,
    ]
    assert overview.latest_overview(db_session, sourced.id).number == 2


def test_rerun_after_store_writes_nothing(app, db_session, server, sourced):
    server.generated = script(1, 1)
    job = run_overview(app, db_session, sourced)
    with app.app_context():
        session = get_session()
        row = session.get(Job, job.id)
        teacher = session.get(Teacher, row.teacher_id)
        generate_overview(jobs.JobContext(session, row, teacher, row.claims))
    db_session.expire_all()
    assert len(versions(db_session, sourced)) == 1
