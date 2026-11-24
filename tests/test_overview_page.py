import re
from datetime import date, time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from chalkboard import jobs, overview, plans
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.ai.write import DraftSentence
from chalkboard.models import (
    Approval,
    Chunk,
    Citation,
    ClassTime,
    Job,
    PlanVersion,
    Sentence,
    Subject,
    SyllabusUnit,
    Teacher,
)
from chalkboard.overview_page import (
    NOT_LATEST,
    OLLAMA_DOWN,
    add_prefill,
    bar_parts,
    check_banner,
    classes_label,
    coverage_chip,
    coverage_rows,
    flagged_sources,
    format_hours,
    progress_steps,
    term_line,
)
from chalkboard.passwords import hash_password
from chalkboard.plans import DraftItem
from tests.conftest import csrf_token, sign_in
from tests.plan_fixtures import add_source

PASSAGES = [
    "A force changes the motion of an object.",
    "Velocity is the rate of change of position.",
    "Energy is conserved in an isolated system.",
]
SUPPORTED = {"supported": True, "reason": "It says so"}


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
    db_session.add_all(
        SyllabusUnit(source_id=syllabus[0].source_id, position=i, number=i + 1, title=t)
        for i, t in enumerate(["Motion", "Energy"])
    )
    add_source(db_session, subject, 2, PASSAGES)
    db_session.commit()
    return subject


def run_all(app):
    while jobs.run_once(app):
        pass


def script(*hours):
    """Outline, one write answer per chapter, then a supported check per sentence."""
    n = len(hours)
    outline = {
        "chapters": [
            {"title": f"Chapter {i + 1}", "unit": i + 1, "hours": h}
            for i, h in enumerate(hours)
        ]
    }
    written = [
        {
            "sentences": [
                {"text": f"Chapter {c} says {k}.", "passages": ["P1"]} for k in (1, 2)
            ]
        }
        for c in range(1, n + 1)
    ]
    return [outline, *written, *([SUPPORTED] * (2 * n))]


def timetable(db_session, subject, end=date(2026, 12, 29)):
    """Tuesdays and Thursdays from Thu 19 Nov 2026: 12 classes up to 29 Dec."""
    subject.class_times = [
        ClassTime(weekday=1, start=time(9)),
        ClassTime(weekday=3, start=time(9)),
    ]
    subject.term_start, subject.term_end = date(2026, 11, 19), end
    db_session.commit()


def test_format_hours():
    assert format_hours(None) == "–"
    assert format_hours(Decimal("3.0")) == "3 h"
    assert format_hours(Decimal("2.5")) == "2.5 h"


def test_classes_label():
    assert classes_label(None) == "Estimate"
    assert classes_label(1) == "1 class · Estimate"
    assert classes_label(3) == "3 classes · Estimate"


def test_flagged_sources():
    assert flagged_sources("Source 3: x; Source 1: y") == {1, 3}
    assert flagged_sources(None) == set()
    assert flagged_sources("") == set()


def test_check_banner():
    assert check_banner(1)[0] == "1 sentence needs checking."
    assert check_banner(2) == (
        "2 sentences need checking.",
        "Their passages say something different.",
    )


def test_bar_parts():
    assert bar_parts(480, 960) == (50, 0)
    assert bar_parts(960, 960) == (100, 0)
    assert bar_parts(1080, 960) == (89, 11)
    assert bar_parts(60, 0) == (0, 100)
    assert bar_parts(0, 0) == (0, 0)


def test_term_line():
    times = {1: time(9), 3: time(9)}
    first, last = date(2026, 11, 19), date(2027, 1, 12)
    assert term_line(16, times, first, last) == (
        "16 classes · Tue, Thu 09:00 · 19 Nov – 12 Jan"
    )
    assert term_line(1, times, first, last).startswith("1 class ·")


def test_progress_steps():
    def states(step):
        job = SimpleNamespace(step=step)
        return [(s[1], s[2]) for s in progress_steps(job, 3)]

    assert states(None) == [("current", ""), ("todo", ""), ("todo", "")]
    assert states("Writing chapter 3 of 4") == [
        ("done", ""),
        ("current", "Chapter 3 of 4"),
        ("todo", ""),
    ]
    assert states(overview.STEP_CHECK) == [
        ("done", ""),
        ("done", ""),
        ("current", ""),
    ]
    assert progress_steps(SimpleNamespace(step=None), 1)[0][0] == (
        "Finding passages in your 1 source"
    )


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def url(subject, tail=""):
    return f"/subjects/{subject.id}/overview{tail}"


def post(client, subject, tail, **data):
    token = csrf_token(client, url(subject))
    return client.post(url(subject, tail), data={"csrf_token": token, **data})


def html(client, path, **kw):
    return client.get(path, **kw).get_data(as_text=True)


def make_version(db_session, subject, hours=("3",), flag=None, chunk=None):
    """A version with one chapter per hours value, each with one sentence citing source 2."""
    chunk = (
        chunk
        or db_session.scalars(select(Chunk).order_by(Chunk.id).offset(1).limit(1)).one()
    )
    sentences = [
        DraftSentence(
            f"Sentence {i + 1}.",
            (chunk.id,),
            i,
            check_flag=bool(flag) and i == 0,
            check_reason=flag if i == 0 else None,
        )
        for i in range(len(hours))
    ]
    items = [
        DraftItem("chapter", f"Motion {i + 1}", Decimal(h), 1)
        for i, h in enumerate(hours)
    ]
    version = plans.store_version(
        db_session, subject, "overview", sentences, items=items
    )
    db_session.commit()
    return version


def test_overview_requires_sign_in(client, sourced):
    r = client.get(url(sourced))
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_other_teachers_subject_404(client, db_session, sourced):
    db_session.add(
        Teacher(
            email="b@example.com", name="B", password_hash=hash_password("pw pw pw pw")
        )
    )
    db_session.commit()
    sign_in(client, "b@example.com", "pw pw pw pw")
    assert client.get(url(sourced)).status_code == 404


def test_no_sources_state(signed_in, subject):
    page = html(signed_in, url(subject))
    assert "Add sources first" in page
    assert f'href="/subjects/{subject.id}/sources"' in page
    assert "Generate" not in page


def test_generate_queues_job_and_shows_progress(signed_in, db_session, server, sourced):
    page = html(signed_in, url(sourced))
    assert "No overview yet" in page and "Generate" in page
    r = post(signed_in, sourced, "/generate")
    assert r.status_code == 303
    jobs = db_session.scalars(select(Job).where(Job.kind == "generate_overview")).all()
    assert len(jobs) == 1
    page = html(signed_in, url(sourced))
    assert "Writing your course overview" in page and "Writing version 1" in page
    assert signed_in.get(url(sourced, "/progress")).status_code == 200


def test_progress_204_when_done(signed_in, sourced):
    assert signed_in.get(url(sourced, "/progress")).status_code == 204


def test_generate_then_request_then_approve_versions(
    app, signed_in, db_session, server, sourced
):
    server.generated = script(3, 1)
    post(signed_in, sourced, "/generate")
    run_all(app)
    db_session.expire_all()
    [v1] = db_session.scalars(select(PlanVersion)).all()
    assert v1.number == 1
    server.generated = script(2, 2)
    post(signed_in, sourced, "/request", version="1", change="Make it shorter")
    job = db_session.scalars(select(Job).order_by(Job.id.desc())).first()
    assert job.payload["parent_version_id"] == v1.id
    run_all(app)
    db_session.expire_all()
    assert sorted(v.number for v in db_session.scalars(select(PlanVersion))) == [1, 2]
    post(signed_in, sourced, "/approve", version="2")
    assert db_session.scalars(select(Approval)).one()
    page = html(signed_in, url(sourced))
    assert "Approved · version 2" in page and "Versions (2)" in page
    assert "Approve version" not in page


def test_older_version_read_only(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    make_version(db_session, sourced)
    page = html(signed_in, url(sourced), query_string={"version": 1})
    assert 'name="change"' not in page and "Approve version" not in page
    assert "Version 1 is read-only" in page
    r = post(signed_in, sourced, "/approve", version="1")
    assert r.status_code == 303
    assert db_session.scalars(select(Approval)).first() is None
    assert NOT_LATEST in html(signed_in, url(sourced))
    assert signed_in.get(url(sourced), query_string={"version": 9}).status_code == 404


def test_class_counts_and_budget(signed_in, db_session, sourced):
    make_version(db_session, sourced, hours=("3", "2.5"))
    sourced.class_minutes = 45
    sourced.class_times = [ClassTime(weekday=1, start=time(9))]
    sourced.term_start, sourced.term_end = date(2026, 11, 19), date(2026, 12, 29)
    db_session.commit()
    page = html(signed_in, url(sourced))
    assert page.count("4 classes · Estimate") == 2
    assert "chip chip-flag" in page and "after the term ends" in page
    assert "more than the term has" in page
    assert f'href="/subjects/{sourced.id}/settings"' in page
    assert "Approve version 1" in page


def test_under_budget_shows_free(signed_in, db_session, sourced):
    make_version(db_session, sourced, hours=("4",))
    timetable(db_session, sourced)
    assert "4 h of 12 h planned · 8 h free" in html(signed_in, url(sourced))


def test_no_timetable(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    page = html(signed_in, url(sourced))
    assert "No timetable yet" in page and "Add class times" in page
    assert f'href="/subjects/{sourced.id}/settings"' in page


def test_citation_links_and_passage(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    citation = db_session.scalars(select(Citation)).one()
    page = html(signed_in, url(sourced))
    assert 'aria-label="Citation, source 2"' in page
    part = html(
        signed_in,
        url(sourced, f"/citations/{citation.id}"),
        query_string={"version": 1},
    )
    assert "Source 2, passage 1" in part and "source-2.txt" in part
    assert "A force changes the motion" in part
    opened = html(signed_in, url(sourced), query_string={"cite": citation.id})
    tag = re.search(r'<div id="passage-panel"[^>]*>', opened).group(0)
    assert "hidden" not in tag and "A force changes the motion" in opened
    closed = html(signed_in, url(sourced))
    assert "hidden" in re.search(r'<div id="passage-panel"[^>]*>', closed).group(0)
    other = Subject(
        teacher_id=sourced.teacher_id, name="Maths", class_minutes=60, colour="#3A57C9"
    )
    db_session.add(other)
    db_session.commit()
    chunks = add_source(db_session, other, 1, ["x"])
    db_session.commit()
    version = make_version(db_session, other, chunk=chunks[0])
    foreign = db_session.scalar(
        select(Citation).join(Sentence).where(Sentence.version_id == version.id)
    )
    r = signed_in.get(
        url(sourced, f"/citations/{foreign.id}"), query_string={"version": 1}
    )
    assert r.status_code == 404


def test_deleted_source_passage(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    citation = db_session.scalars(select(Citation)).one()
    citation.chunk_id = None
    db_session.commit()
    part = html(
        signed_in,
        url(sourced, f"/citations/{citation.id}"),
        query_string={"version": 1},
    )
    assert "Source 2 · source deleted" in part


def test_check_this(signed_in, db_session, sourced):
    make_version(db_session, sourced, flag="Source 2: says something else")
    citation = db_session.scalars(select(Citation)).one()
    page = html(signed_in, url(sourced))
    assert "is-flagged" in page and "cite is-flag" in page and "Check this" in page
    assert "1 sentence needs checking." in page
    part = html(
        signed_in,
        url(sourced, f"/citations/{citation.id}"),
        query_string={"version": 1},
    )
    assert "says something else" in part and "Ask to fix this sentence" in part
    sentence = db_session.scalars(select(Sentence)).one()
    page = html(signed_in, url(sourced), query_string={"fix": sentence.id})
    assert "Fix this sentence in Chapter 1:" in page


def test_ollama_down_disables_and_refuses(
    signed_in, db_session, teacher, server, sourced
):
    teacher.ollama_status = "unreachable"
    db_session.commit()
    assert "Can't reach the Ollama server at" in html(signed_in, url(sourced))
    assert "disabled" in html(signed_in, url(sourced))
    post(signed_in, sourced, "/generate")
    assert (
        db_session.scalars(select(Job).where(Job.kind == "generate_overview")).first()
        is None
    )
    assert OLLAMA_DOWN.replace("'", "&#39;") in html(signed_in, url(sourced))
    make_version(db_session, sourced)
    page = html(signed_in, url(sourced))
    assert 'class="btn" disabled' in page
    post(signed_in, sourced, "/request", version="1", change="Shorter")
    assert (
        db_session.scalars(select(Job).where(Job.kind == "generate_overview")).first()
        is None
    )
    post(signed_in, sourced, "/approve", version="1")
    assert db_session.scalars(select(Approval)).one()


def test_request_change_validation(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    r = post(signed_in, sourced, "/request", version="1", change="  ")
    assert r.status_code == 422
    assert "Say what should change" in r.get_data(as_text=True)


def test_failed_job_banner(signed_in, db_session, sourced):
    job = Job(
        teacher_id=sourced.teacher_id,
        kind="generate_overview",
        payload={"subject_id": sourced.id},
        state="failed",
        error="X broke",
    )
    db_session.add(job)
    db_session.commit()
    page = html(signed_in, url(sourced))
    assert "X broke" in page and "Generate" in page


def test_sidebar_links_overview(signed_in, sourced):
    page = html(signed_in, url(sourced))
    assert re.search(
        rf'href="/subjects/{sourced.id}/overview"[^>]*aria-current="page"', page
    )


def unit_by_title(db_session, title):
    return db_session.scalars(
        select(SyllabusUnit).where(SyllabusUnit.title == title)
    ).one()


def fake_units(*numbers):
    return [
        SimpleNamespace(number=n, position=i, other_term=False)
        for i, n in enumerate(numbers)
    ]


def fake_items(*numbers):
    return [SimpleNamespace(unit_number=n) for n in numbers]


def test_coverage_rows_and_chip():
    units = fake_units(1, 2, 3)
    rows = coverage_rows(units, fake_items(1, 1, 3))
    assert [r.chapters for r in rows] == [[1, 2], [], [3]]
    assert [r.uncovered for r in rows] == [False, True, False]
    assert coverage_chip(rows) == "2 of 3 units"
    units[1].other_term = True
    assert not rows[1].uncovered


def test_add_prefill():
    units = fake_units(1, 2, 3)
    assert (
        add_prefill(units[0], units, fake_items(2, 3))
        == "Add a chapter for Unit 1 before Chapter 1"
    )
    assert (
        add_prefill(units[2], units, fake_items(1, 2))
        == "Add a chapter for Unit 3 after Chapter 2"
    )
    assert add_prefill(units[0], units, []) == "Add a chapter for Unit 1"


def test_coverage_flags_uncovered_unit(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    page = html(signed_in, url(sourced))
    assert "Coverage" in page and "1 of 2 units" in page
    assert re.search(r"Not covered</span>\s*</p>\s*<p[^>]*><strong>Energy", page)
    assert "Ask to add it" in page and "Not this term" in page
    assert "Ch 1" in page


def test_ask_to_add_prefills_request(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    energy = unit_by_title(db_session, "Energy")
    page = html(signed_in, url(sourced, f"?add={energy.id}"))
    assert "Add a chapter for Unit 2 after Chapter 1" in page


def test_not_this_term_persists_and_undo(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    energy = unit_by_title(db_session, "Energy")
    r = post(signed_in, sourced, f"/units/{energy.id}/term", other_term="1")
    assert r.status_code == 303
    db_session.refresh(energy)
    assert energy.other_term is True
    page = html(signed_in, url(sourced))
    assert "Not covered" not in page
    assert '<span class="chip">Not this term</span>' in page
    assert "1 of 2 units" in page
    assert re.search(r'chip chip-flag">1 of 2', page) is None
    post(signed_in, sourced, f"/units/{energy.id}/term", other_term="0")
    db_session.refresh(energy)
    assert energy.other_term is False
    assert "Not covered" in html(signed_in, url(sourced))


def test_not_this_term_requires_csrf_and_own_unit(signed_in, db_session, sourced):
    energy = unit_by_title(db_session, "Energy")
    r = signed_in.post(
        url(sourced, f"/units/{energy.id}/term"), data={"other_term": "1"}
    )
    assert r.status_code == 400
    other = Subject(
        teacher_id=sourced.teacher_id, name="Maths", class_minutes=60, colour="#0E7A64"
    )
    db_session.add(other)
    db_session.commit()
    assert (
        post(signed_in, other, f"/units/{energy.id}/term", other_term="1").status_code
        == 404
    )


def test_unit_chip_opens_unit_text(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    motion = unit_by_title(db_session, "Motion")
    page = html(signed_in, url(sourced))
    assert 'class="chip chip-primary unit-cite"' in page
    assert f'data-panel="{url(sourced, f"/units/{motion.id}")}"' in page
    panel = html(signed_in, url(sourced, f"/units/{motion.id}"))
    assert (
        "Syllabus · Unit 1" in panel and "Motion" in panel and "source-1.txt" in panel
    )
    opened = html(signed_in, url(sourced, f"?unit={motion.id}"))
    assert re.search(r'id="passage-panel"[^>]*>', opened).group(0).count("hidden") == 0
    assert "Unit I Motion" in opened


def test_no_syllabus_state(signed_in, db_session, subject):
    add_source(db_session, subject, 1, PASSAGES)
    db_session.commit()
    make_version(db_session, subject)
    page = html(signed_in, url(subject))
    assert "Not added yet" in page and "Add a syllabus" in page
    assert "Coverage" not in page and "unit-cite" not in page


def test_board_none_state(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    motion = unit_by_title(db_session, "Motion")
    sourced.board = "none"
    db_session.commit()
    page = html(signed_in, url(sourced))
    assert "Syllabus" not in page and "Coverage" not in page
    assert signed_in.get(url(sourced, f"/units/{motion.id}")).status_code == 404


def test_read_only_version_hides_coverage_actions(signed_in, db_session, sourced):
    make_version(db_session, sourced)
    make_version(db_session, sourced)
    page = html(signed_in, url(sourced, "?version=1"))
    assert "Coverage" in page and "Not covered" in page
    assert "Ask to add it" not in page and "Not this term" not in page
