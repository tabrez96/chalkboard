from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chalkboard import jobs
from chalkboard import sources as sources_module
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.base import FormatMismatch, OllamaError
from chalkboard.ai.fake import FakeLLM, FakeOllama
from chalkboard.models import Chunk, Source, Subject, SyllabusUnit
from chalkboard.syllabus import (
    FoundUnit,
    find_units,
    heading_units,
    llm_units,
    parse_units,
    roman_to_int,
    set_other_term,
    subject_unit,
    syllabus_status,
    type_units,
    unit_passage,
    unit_rows,
    use_as_syllabus,
)
from tests.plan_fixtures import add_source
from tests.test_sources import refresh, upload

CBSE = """Physics Class XI
Unit–I Physical World and Measurement 08
Unit–II Kinematics 13
Unit–III Laws of Motion 14
Unit–IV Work, Energy and Power 12
Unit–V Gravitation 10
Course structure follows.
Unit I: Physical World and Measurement
Chapter 1: Units and Measurements
Unit II: Kinematics
"""
CBSE_TXT = CBSE.encode()
NO_HEADINGS = b"Motion and forces are covered this year. Waves follow in term two."


@pytest.fixture
def data(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.extensions["chalkboard.settings"], "data_dir", tmp_path)
    return tmp_path


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


def run_all(app):
    while jobs.run_once(app):
        pass


def titles(units):
    return [(u.number, u.title) for u in units]


def test_heading_units_cbse_style():
    units = heading_units(CBSE)
    assert titles(units) == [
        (1, "Physical World and Measurement"),
        (2, "Kinematics"),
        (3, "Laws of Motion"),
        (4, "Work, Energy and Power"),
        (5, "Gravitation"),
    ]


def test_heading_units_roman_numerals():
    text = "UNIT IV. Work, Energy and Power\nUnit IX – Gravitation\nunit vi\nThermodynamics\n"
    assert titles(heading_units(text)) == [
        (4, "Work, Energy and Power"),
        (9, "Gravitation"),
        (6, "Thermodynamics"),
    ]


def test_heading_units_arabic_and_title_on_next_line():
    text = "Chapter 1\n\nSets\nChapter 2: Relations and Functions\n"
    assert titles(heading_units(text)) == [(1, "Sets"), (2, "Relations and Functions")]


def test_heading_units_needs_two():
    assert heading_units("Chapter 3 shows how forces add up.") == []


def test_heading_units_skips_number_only_headings():
    assert heading_units("Unit 1\nUnit 2\nUnit 3 Waves\n") == []


def test_roman_to_int():
    assert [roman_to_int(t) for t in ("I", "IV", "IX", "XII", "xl")] == [
        1,
        4,
        9,
        12,
        40,
    ]
    assert [roman_to_int(t) for t in ("IIII", "VX", "", "ABC")] == [None] * 4


def test_llm_fallback_with_fake_llm():
    llm = FakeLLM(
        [
            {
                "units": [
                    {"number": 1, "title": "Motion"},
                    {"number": 2, "title": "Forces"},
                ]
            }
        ]
    )
    units = find_units(llm, "Motion first, then forces.")
    assert titles(units) == [(1, "Motion"), (2, "Forces")]
    assert [c[0] for c in llm.calls] == ["generate"]


def test_llm_not_called_when_heuristic_finds_units():
    llm = FakeLLM()
    assert len(find_units(llm, CBSE)) == 5
    assert llm.calls == []


def test_llm_not_called_for_blank_text():
    llm = FakeLLM()
    assert find_units(llm, "  \n ") == []
    assert llm.calls == []


def test_parse_units_drops_junk():
    answer = {
        "units": [
            "text",
            {"number": "2", "title": "Str number"},
            {"number": True, "title": "Bool"},
            {"number": 0, "title": "Zero"},
            {"number": 3, "title": "   "},
            {"number": 4, "title": 7},
            {"number": 5, "title": " Waves  and\nSound "},
            {"number": 5, "title": "Duplicate"},
        ]
    }
    assert titles(parse_units(answer)) == [(5, "Waves and Sound")]
    assert parse_units("x") == []
    assert parse_units({"units": "x"}) == []


def test_neither_finds_units_gives_no_units_found(
    app,
    db_session,
    subject,
    data,
    server,
):
    source = upload(app, db_session, subject, content=NO_HEADINGS)
    use_as_syllabus(db_session, subject, source)
    run_all(app)
    refresh(db_session, source)
    assert unit_rows(db_session, source.id) == []
    assert syllabus_status(source, 0) == "No units found"


def test_ingest_chains_detection_for_syllabus(app, db_session, subject, data, server):
    source = upload(app, db_session, subject, content=CBSE_TXT)
    use_as_syllabus(db_session, subject, source)
    assert source.job.kind == "ingest_source"
    run_all(app)
    refresh(db_session, source)
    rows = unit_rows(db_session, source.id)
    assert len(rows) == 5 and not any(r.typed for r in rows)
    assert syllabus_status(source, len(rows)) == "Ready · 5 units"


def test_llm_units_stored_through_the_job(app, db_session, subject, data, server):
    server.generated = [{"units": [{"number": 1, "title": "Motion"}]}]
    source = upload(app, db_session, subject, content=NO_HEADINGS)
    run_all(app)
    use_as_syllabus(db_session, subject, source)
    run_all(app)
    assert titles(unit_rows(db_session, source.id)) == [(1, "Motion")]
    refresh(db_session, source)
    assert syllabus_status(source, 1) == "Ready · 1 unit"


def test_use_as_syllabus_on_ready_source_queues_detection(
    app, db_session, subject, data, server
):
    source = upload(app, db_session, subject, content=CBSE_TXT)
    run_all(app)
    use_as_syllabus(db_session, subject, source)
    assert source.job.kind == "detect_units"
    use_as_syllabus(db_session, subject, source)
    assert source.job.kind == "detect_units"


def test_one_syllabus_per_subject_enforced_by_database(db_session, subject):
    for number in (1, 2):
        db_session.add(
            Source(
                subject_id=subject.id,
                number=number,
                name=f"s{number}.txt",
                kind="txt",
                file_path="",
                is_syllabus=True,
            )
        )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_replace_returns_old_to_numbered_source(app, db_session, subject, data, server):
    first = upload(app, db_session, subject, content=CBSE_TXT)
    second = upload(app, db_session, subject, content=NO_HEADINGS)
    run_all(app)
    before = db_session.query(Chunk).filter_by(source_id=first.id).count()
    use_as_syllabus(db_session, subject, first)
    use_as_syllabus(db_session, subject, second)
    db_session.refresh(subject)
    db_session.refresh(first)
    assert (first.is_syllabus, first.number) == (False, 1)
    assert db_session.query(Chunk).filter_by(source_id=first.id).count() == before > 0
    assert second.is_syllabus
    assert subject.source_counter == 2


def seeded_syllabus(app, db_session, subject):
    source = upload(app, db_session, subject, content=CBSE_TXT)
    use_as_syllabus(db_session, subject, source)
    run_all(app)
    return source


def test_typed_units_replace_detected_and_are_typed(
    app, db_session, subject, data, server
):
    source = seeded_syllabus(app, db_session, subject)
    assert type_units(db_session, source, "Motion\n\n Forces ") == 2
    rows = unit_rows(db_session, source.id)
    assert [(r.number, r.title, r.typed) for r in rows] == [
        (1, "Motion", True),
        (2, "Forces", True),
    ]
    assert len(db_session.scalars(select(SyllabusUnit)).all()) == 2


def test_type_units_rejects_blank(db_session, subject):
    source = Source(
        subject_id=subject.id, number=1, name="a.txt", kind="txt", file_path=""
    )
    db_session.add(source)
    db_session.commit()
    with pytest.raises(ValueError, match="at least one unit"):
        type_units(db_session, source, "  \n ")


def test_detection_never_overwrites_typed_units(app, db_session, subject, data, server):
    source = seeded_syllabus(app, db_session, subject)
    type_units(db_session, source, "Only mine")
    source.job = jobs.enqueue(
        db_session, subject.teacher_id, "detect_units", {"source_id": source.id}
    )
    db_session.commit()
    run_all(app)
    assert [(r.title, r.typed) for r in unit_rows(db_session, source.id)] == [
        ("Only mine", True)
    ]


def test_detect_units_waits_when_ollama_down(app, db_session, subject, data, server):
    source = upload(app, db_session, subject, content=NO_HEADINGS)
    run_all(app)
    use_as_syllabus(db_session, subject, source)
    server.up = False
    jobs.run_once(app)
    refresh(db_session, source)
    assert source.job.state == "waiting_ollama"
    assert unit_rows(db_session, source.id) == []


def test_fake_ollama_generate_answers_in_order():
    fake = FakeOllama(generated=[{"a": 1}])
    assert fake.generate("p", {}) == {"a": 1}
    assert fake.generate("p", {}) == {}
    fake.up = False
    with pytest.raises(Exception, match="down"):
        fake.generate("p", {})


def test_found_unit_is_hashable():
    assert FoundUnit(1, "A") == FoundUnit(1, "A")


def other_session(db_connection):
    return Session(bind=db_connection, join_transaction_mode="create_savepoint")


class BrokenLLM:
    def __init__(self, error):
        self.error = error

    def generate(self, prompt, schema):
        raise self.error


def test_heading_units_prefer_units_over_chapters():
    text = (
        "Chapter 2 Vectors\nChapter 3 Algebra\nUnit I Mechanics\nUnit II Kinematics\n"
    )
    assert titles(heading_units(text)) == [(1, "Mechanics"), (2, "Kinematics")]


def test_heading_units_chapters_alone_still_found():
    assert titles(heading_units("Chapter 1 Sets\nChapter 2 Functions\n")) == [
        (1, "Sets"),
        (2, "Functions"),
    ]


def test_llm_units_format_mismatch_is_no_units():
    assert llm_units(BrokenLLM(FormatMismatch("bad")), "Some text") == []


def test_llm_units_service_error_reaches_the_job_runner():
    with pytest.raises(OllamaError):
        llm_units(BrokenLLM(OllamaError("HTTP 500")), "Some text")


def test_failed_detect_job_offers_no_units_found():
    job = SimpleNamespace(state="failed", kind="detect_units", error="x", progress=0)
    assert syllabus_status(SimpleNamespace(job=job, passages=3), 0) == "No units found"
    job.kind = "ingest_source"
    assert syllabus_status(SimpleNamespace(job=job, passages=3), 0) == (
        "Couldn't read the file"
    )


def test_mark_during_ingest_still_queues_detection(
    app, db_session, db_connection, subject, data, server, monkeypatch
):
    source = upload(app, db_session, subject, content=CBSE_TXT)
    real_chunk = sources_module.chunk

    def chunk_after_mark(sections):
        # The teacher marks the source while ingest runs: the worker's loaded row still says False.
        with other_session(db_connection) as other:
            use_as_syllabus(
                other, other.get(Subject, subject.id), other.get(Source, source.id)
            )
        return real_chunk(sections)

    monkeypatch.setattr(sources_module, "chunk", chunk_after_mark)
    run_all(app)
    assert len(unit_rows(db_session, source.id)) == 5


def test_stale_session_mark_ends_with_that_source_as_syllabus(
    app, db_session, db_connection, subject, data, server
):
    first = upload(app, db_session, subject, content=CBSE_TXT)
    second = upload(app, db_session, subject, name="b.txt", content=CBSE_TXT)
    # Like a request session (chalkboard/db.py): nothing is expired on commit, so it goes stale.
    stale = Session(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    stale_subject = stale.get(Subject, subject.id)
    stale_first = stale.get(Source, first.id)
    use_as_syllabus(stale, stale_subject, stale_first)
    with other_session(db_connection) as other:
        use_as_syllabus(
            other, other.get(Subject, subject.id), other.get(Source, second.id)
        )
    assert stale_first.is_syllabus  # this session has not seen the replacement
    use_as_syllabus(stale, stale_subject, stale_first)
    stale.close()
    flags = dict(
        db_session.execute(
            select(Source.id, Source.is_syllabus).execution_options(
                populate_existing=True
            )
        ).all()
    )
    assert flags == {first.id: True, second.id: False}


def unit_with_title(db_session, source_id, title, position=0):
    unit = SyllabusUnit(source_id=source_id, position=position, number=1, title=title)
    db_session.add(unit)
    db_session.flush()
    return unit


def test_unit_passage_finds_first_matching_chunk(db_session, subject):
    chunks = add_source(
        db_session,
        subject,
        1,
        ["Intro", "Unit 2 Energy and work", "Energy again", "50 percent"],
        is_syllabus=True,
    )
    source_id = chunks[0].source_id
    assert (
        unit_passage(db_session, unit_with_title(db_session, source_id, "Energy"))
        == chunks[1]
    )
    assert (
        unit_passage(db_session, unit_with_title(db_session, source_id, "Optics", 1))
        is None
    )
    assert (
        unit_passage(db_session, unit_with_title(db_session, source_id, "50%_x", 2))
        is None
    )


def test_subject_unit_scoped_to_current_syllabus(db_session, subject):
    source_id = add_source(db_session, subject, 1, ["Unit 1 Motion"], is_syllabus=True)[
        0
    ].source_id
    unit = unit_with_title(db_session, source_id, "Motion")
    other = Subject(
        teacher_id=subject.teacher_id, name="Maths", class_minutes=60, colour="#0E7A64"
    )
    db_session.add(other)
    db_session.flush()
    assert subject_unit(db_session, subject, unit.id) == unit
    assert subject_unit(db_session, other, unit.id) is None
    db_session.get(Source, source_id).is_syllabus = False
    db_session.flush()
    assert subject_unit(db_session, subject, unit.id) is None


def test_set_other_term(db_session, subject):
    source_id = add_source(db_session, subject, 1, ["Unit 1 Motion"], is_syllabus=True)[
        0
    ].source_id
    unit = unit_with_title(db_session, source_id, "Motion")
    assert unit.other_term is False
    set_other_term(db_session, unit, True)
    assert unit.other_term is True
