import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from chalkboard import jobs, plans
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.ai.write import DraftSentence
from chalkboard.chapters import (
    APPROVED,
    DRAFT,
    NOT_WRITTEN,
    SPLIT_KIND,
    added_key,
    chapter_versions,
    title_key,
)
from chalkboard.chapters_page import (
    HAS_DRAFT,
    approve_label,
    check_banner,
    hours_line,
    progress_steps,
    split_line,
    status_chip,
    time_ranges,
)
from chalkboard.models import (
    Approval,
    Chunk,
    Citation,
    Job,
    Sentence,
    Source,
    Subject,
    SyllabusUnit,
    Teacher,
)
from chalkboard.overview import STEP_CHECK
from chalkboard.overview_page import NOT_LATEST, OLLAMA_DOWN
from chalkboard.passwords import hash_password
from chalkboard.plans import DraftItem, store_version
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


def run_all(app):
    while jobs.run_once(app):
        pass


def chunk_id(db_session, subject):
    return db_session.scalar(
        select(Chunk.id)
        .join(Source, Chunk.source_id == Source.id)
        .where(Source.subject_id == subject.id, Source.is_syllabus.is_(False))
        .limit(1)
    )


def approve_overview(db_session, subject, titles, hours=Decimal("4.0")):
    cid = chunk_id(db_session, subject)
    items = [DraftItem("chapter", t, hours, 1) for t in titles]
    sentences = [
        DraftSentence(f"{t} is covered.", (cid,), i) for i, t in enumerate(titles)
    ]
    version = store_version(db_session, subject, "overview", sentences, items=items)
    plans.approve(db_session, version)
    db_session.commit()
    return version


def store_chapter(db_session, subject, key, title="Forces", flag=False):
    cid = chunk_id(db_session, subject)
    sentences = [
        DraftSentence("Forces push and pull.", (cid,), 1),
        DraftSentence(
            "Heavy things fall faster.",
            (cid,),
            2,
            check_flag=flag,
            check_reason="Source 2: says something else" if flag else None,
        ),
    ]
    version = store_version(
        db_session,
        subject,
        "chapter",
        sentences,
        items=[
            DraftItem("chapter", title, Decimal("4.0")),
            DraftItem("section", "What a force is"),
            DraftItem("section", "Falling objects"),
        ],
        chapter_key=key,
    )
    db_session.commit()
    return version


def written():
    return {
        "sentences": [
            {"text": f"Section says {k}.", "passages": ["P1"]} for k in (1, 2)
        ]
    }


def script():
    return [
        {"sections": [{"title": "S1"}, {"title": "S2"}]},
        written(),
        written(),
        *([SUPPORTED] * 4),
    ]


def url(subject, tail=""):
    return f"/subjects/{subject.id}/chapters{tail}"


def post(client, subject, tail, **data):
    token = csrf_token(client, url(subject))
    return client.post(url(subject, tail), data={"csrf_token": token, **data})


def html(client, path, **kw):
    return client.get(path, **kw).get_data(as_text=True)


def jobs_of(db_session):
    return list(db_session.scalars(select(Job).where(Job.kind == "generate_chapter")))


def entry(status):
    return SimpleNamespace(status=status)


KEY = title_key("Forces")


def test_status_chip():
    assert status_chip(entry(NOT_WRITTEN), None) == ("Not written", "chip")
    assert status_chip(entry(DRAFT), 2) == ("Draft v2", "chip chip-draft")
    assert status_chip(entry(APPROVED), 2) == ("Approved", "chip chip-ok")


def test_check_banner():
    one = check_banner(1)
    assert one[0] == "1 sentence needs checking." and one[2] == "Show it"
    two = check_banner(2)
    assert two[0] == "2 sentences need checking." and two[2] == "Show them"
    assert "Their cited passages" in two[1]


def test_hours_line():
    assert hours_line(None, 60) == ""
    assert hours_line(Decimal("4.0"), 60) == "4 hours · will become 4 classes of 60 min"
    assert hours_line(Decimal("1.0"), 60) == "1 hour · will become 1 class of 60 min"
    assert hours_line(Decimal("2.5"), 60).startswith("2.5 hours · will become 3")


def test_progress_steps():
    def steps(step, n=1):
        return progress_steps(SimpleNamespace(kind="generate_chapter", step=step), n)

    first = steps(None)
    assert [s[1] for s in first] == ["current", "todo", "todo"]
    assert first[0][0] == "Finding passages in your 1 source"
    mid = steps("Writing section 2 of 3", 2)
    assert [s[1] for s in mid] == ["done", "current", "todo"]
    assert mid[1][2] == "Section 2 of 3" and mid[0][2] == ""
    assert mid[0][0] == "Finding passages in your 2 sources"
    assert [s[1] for s in steps(STEP_CHECK)] == ["done", "done", "current"]


def test_no_approved_overview_shows_empty_list(signed_in, db_session, sourced):
    cid = chunk_id(db_session, sourced)
    store_version(
        db_session,
        sourced,
        "overview",
        [DraftSentence("X is covered.", (cid,), 0)],
        items=[DraftItem("chapter", "X", Decimal("2.0"), 1)],
    )
    db_session.commit()
    r = signed_in.get(url(sourced))
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Approve the course overview first" in page
    assert f'href="/subjects/{sourced.id}/overview"' in page
    assert "Add a chapter" not in page
    assert signed_in.get(url(sourced, "/new")).status_code == 303


def test_list_and_next_card_prefilled(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces", "Momentum"])
    page = html(signed_in, url(sourced))
    assert "Chapter 1" in page and "Chapter 2" in page
    assert page.count("Not written") == 2
    assert "4 h" in page and "Next chapter" in page
    assert 'value="Forces"' in page and "Write chapter draft" in page
    assert "Add a chapter" in page


def test_sidebar_links_chapters(signed_in, db_session, sourced):
    assert f'href="/subjects/{sourced.id}/chapters"' in html(signed_in, url(sourced))


def test_write_rename_and_notes_reach_draft(
    app, signed_in, db_session, server, sourced
):
    approve_overview(db_session, sourced, ["Forces", "Momentum"])
    post(
        signed_in,
        sourced,
        "/write",
        chapter=KEY,
        title="Forces and motion",
        notes="Use sport examples",
    )
    assert len(jobs_of(db_session)) == 1
    assert "Writing the chapter draft" in html(signed_in, url(sourced))
    server.generated = script()
    run_all(app)
    db_session.expire_all()
    version = chapter_versions(db_session, sourced.id, KEY)[0]
    assert version.items[0].title == "Forces and motion"
    assert version.items[0].notes == "Use sport examples"
    page = html(signed_in, url(sourced))
    assert "Draft v1" in page and "Forces and motion" in page


def test_write_validates(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    r = post(signed_in, sourced, "/write", chapter=KEY, title="  ", notes="")
    assert r.status_code == 422
    assert "Give the chapter a title" in r.get_data(as_text=True)
    r = post(
        signed_in, sourced, "/write", chapter=KEY, title="Forces", notes="n" * 2001
    )
    page = r.get_data(as_text=True)
    assert r.status_code == 422 and "Keep the notes under 2000" in page
    assert "write-notes-error" in page
    assert jobs_of(db_session) == []


def test_write_refused_when_draft_exists(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    store_chapter(db_session, sourced, KEY)
    r = post(signed_in, sourced, "/write", chapter=KEY, title="Forces", notes="")
    assert r.status_code == 303
    assert HAS_DRAFT in html(signed_in, url(sourced))
    assert jobs_of(db_session) == []


def test_next_card_review_draft_link(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces", "Momentum"])
    store_chapter(db_session, sourced, KEY)
    page = html(signed_in, url(sourced))
    assert "Review draft" in page
    assert f"{url(sourced)}?chapter={KEY}" in page


def test_all_approved_shows_message(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    plans.approve(db_session, store_chapter(db_session, sourced, KEY))
    db_session.commit()
    assert "All chapters are approved." in html(signed_in, url(sourced))


def test_draft_review_renders_sections_citations_and_flags(
    signed_in, db_session, sourced
):
    approve_overview(db_session, sourced, ["Forces"])
    store_chapter(db_session, sourced, KEY, flag=True)
    page = html(signed_in, url(sourced), query_string={"chapter": KEY})
    for text in (
        "What a force is",
        "Falling objects",
        'class="cite',
        "Check this",
        "1 sentence needs checking.",
        "Show it",
        "Draft · version 1",
        "Versions (1)",
        "What should change?",
        "4 hours · will become 4 classes of 60 min",
    ):
        assert text in page
    assert "Approve and split into 4 classes" in page


def test_passage_route(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    version = store_chapter(db_session, sourced, KEY, flag=True)
    sentence = version.sentences[0]
    cid = sentence.citations[0].id
    q = {"chapter": KEY, "version": 1}
    part = html(signed_in, url(sourced, f"/citations/{cid}"), query_string=q)
    assert "A force changes the motion" in part
    assert (
        signed_in.get(
            url(sourced, f"/citations/{cid}"), query_string={**q, "version": 2}
        ).status_code
        == 404
    )
    opened = html(signed_in, url(sourced), query_string={"chapter": KEY, "cite": cid})
    tag = re.search(r'<div id="passage-panel"[^>]*>', opened).group(0)
    assert "hidden" not in tag and "A force changes the motion" in opened
    flagged = version.sentences[1]
    page = html(
        signed_in, url(sourced), query_string={"chapter": KEY, "fix": flagged.id}
    )
    assert "Fix this sentence in &#34;Falling objects&#34;" in page


def test_request_changes_writes_new_version_and_old_is_read_only(
    app, signed_in, db_session, server, sourced
):
    approve_overview(db_session, sourced, ["Forces"])
    store_chapter(db_session, sourced, KEY)
    post(
        signed_in, sourced, "/request", chapter=KEY, version="1", change="Add friction"
    )
    server.generated = script()
    run_all(app)
    db_session.expire_all()
    versions = chapter_versions(db_session, sourced.id, KEY)
    assert len(versions) == 2
    assert versions[0].parent_version_id == versions[1].id
    assert versions[0].change_request == "Add friction"
    old = html(signed_in, url(sourced), query_string={"chapter": KEY, "version": 1})
    assert "Version 1 is read-only" in old and 'name="change"' not in old
    before = len(jobs_of(db_session))
    post(signed_in, sourced, "/request", chapter=KEY, version="1", change="Again")
    assert NOT_LATEST in html(signed_in, url(sourced))
    assert len(jobs_of(db_session)) == before


def test_request_changes_empty_is_422(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    store_chapter(db_session, sourced, KEY)
    r = post(signed_in, sourced, "/request", chapter=KEY, version="1", change=" ")
    assert r.status_code == 422
    assert "Say what should change" in r.get_data(as_text=True)


def test_generating_state(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    post(signed_in, sourced, "/write", chapter=KEY, title="Forces", notes="")
    page = html(signed_in, url(sourced), query_string={"chapter": KEY})
    assert "Writing the chapter draft" in page
    assert "Finding passages in your 1 source" in page
    assert "Writing version 1" in page
    r = signed_in.get(url(sourced, "/progress"), query_string={"chapter": KEY})
    assert r.status_code == 200 and "progress-card" in r.get_data(as_text=True)
    jobs_of(db_session)[0].state = "done"
    db_session.commit()
    r = signed_in.get(url(sourced, "/progress"), query_string={"chapter": KEY})
    assert r.status_code == 204


def test_failed_job_banner(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    db_session.add(
        Job(
            teacher_id=sourced.teacher_id,
            kind="generate_chapter",
            payload={"subject_id": sourced.id, "chapter_key": KEY},
            state="failed",
            error="X broke",
        )
    )
    db_session.commit()
    page = html(signed_in, url(sourced))
    assert "X broke" in page and "Write chapter draft" in page


def test_ollama_unreachable_state(signed_in, db_session, teacher, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    teacher.ollama_status = "unreachable"
    db_session.commit()
    page = html(signed_in, url(sourced))
    assert "Can't reach the Ollama server at" in page
    assert re.search(r'<button[^>]*btn-primary"[^>]* disabled', page)
    r = post(signed_in, sourced, "/write", chapter=KEY, title="Forces", notes="")
    assert r.status_code == 303
    assert OLLAMA_DOWN.replace("'", "&#39;") in html(signed_in, url(sourced))
    store_chapter(db_session, sourced, KEY)
    assert 'class="btn" disabled' in html(
        signed_in, url(sourced), query_string={"chapter": KEY}
    )
    post(signed_in, sourced, "/request", chapter=KEY, version="1", change="Shorter")
    assert jobs_of(db_session) == []


def test_add_chapter_appends_and_validates(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    assert "Add a chapter" in html(signed_in, url(sourced, "/new"))
    assert "modal-backdrop" in html(signed_in, url(sourced, "/new"))
    r = post(signed_in, sourced, "/new", title="Optics", hours="2.5", notes="Lenses")
    assert r.status_code == 303
    row_id = int(re.search(r"chapter=a(\d+)", r.headers["Location"]).group(1))
    assert added_key(row_id) in r.headers["Location"]
    page = html(signed_in, url(sourced))
    chapter_list = page.split('class="card chapters-list"')[1].split("</nav>")[0]
    assert chapter_list.rfind("Optics") > chapter_list.rfind("Forces")
    assert "2.5 h" in chapter_list
    bad = [
        ({"title": " ", "hours": "2"}, "Give the chapter a title"),
        ({"title": "T", "hours": "0"}, "Hours must be between 0.1 and 999.9"),
        ({"title": "T", "hours": "abc"}, "Not a valid decimal value"),
        ({"title": "T", "notes": "n" * 2001}, "Keep the notes under 2000"),
    ]
    for data, message in bad:
        r = post(signed_in, sourced, "/new", **data)
        assert r.status_code == 400 and message in r.get_data(as_text=True)
    assert html(signed_in, url(sourced)).count("Chapter ") >= 2
    assert html(signed_in, url(sourced)).count('class="chapters-item"') == 2
    r = post(signed_in, sourced, "/new", title="Plain", hours="")
    assert r.status_code == 303
    assert html(signed_in, url(sourced)).count('class="chapters-item"') == 3


def test_unknown_chapter_404(signed_in, client, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    assert (
        signed_in.get(url(sourced), query_string={"chapter": "nope"}).status_code == 404
    )
    assert (
        post(signed_in, sourced, "/write", chapter="nope", title="x").status_code == 404
    )
    assert (
        signed_in.get(
            url(sourced), query_string={"chapter": KEY, "version": 9}
        ).status_code
        == 200
    )
    store_chapter(db_session, sourced, KEY)
    assert (
        signed_in.get(
            url(sourced), query_string={"chapter": KEY, "version": 9}
        ).status_code
        == 404
    )


def test_other_teachers_subject_404(client, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    db_session.add(
        Teacher(
            email="b@example.com", name="B", password_hash=hash_password("pw pw pw pw")
        )
    )
    db_session.commit()
    sign_in(client, "b@example.com", "pw pw pw pw")
    assert client.get(url(sourced)).status_code == 404


def store_lessons(db_session, subject, chapter, count=3, flag=False):
    cid = chunk_id(db_session, subject)
    steps = [("Warm-up", 10), ("Teach", 20), ("Practice", 20), ("Exit", 10)]
    sentences = [
        DraftSentence(
            f"{text} {i + 1}.",
            (cid,),
            i,
            check_flag=flag and i == 0 and j == 0,
            check_reason="Source 2: says something else" if flag else None,
            minutes=m,
        )
        for i in range(count)
        for j, (text, m) in enumerate(steps)
    ]
    version = store_version(
        db_session,
        subject,
        "lessons",
        sentences,
        items=[DraftItem("lesson", f"Class title {i + 1}") for i in range(count)],
        chapter_key=chapter.chapter_key,
        parent=chapter,
    )
    db_session.commit()
    return version


def approved_with_lessons(db_session, subject, flag=False):
    approve_overview(db_session, subject, ["Forces"], Decimal("3.0"))
    chapter = store_chapter(db_session, subject, KEY)
    plans.approve(db_session, chapter)
    db_session.commit()
    return chapter, store_lessons(db_session, subject, chapter, flag=flag)


def split_jobs(db_session):
    return list(db_session.scalars(select(Job).where(Job.kind == SPLIT_KIND)))


def test_split_line_time_ranges_and_approve_label():
    assert split_line(Decimal("3.0"), 60, 3) == "3 h at 60 min per class = 3 classes"
    assert split_line(Decimal("1.0"), 60, 1) == "1 h at 60 min per class = 1 class"
    assert split_line(None, 60, 1) == "1 class of 60 min"
    assert time_ranges([10, 20, 20, 10]) == ["0–10", "10–30", "30–50", "50–60"]
    assert time_ranges([]) == []
    assert (
        approve_label(Decimal("3.0"), 60, False) == "Approve and split into 3 classes"
    )
    assert approve_label(Decimal("3.0"), 60, True) == "Split into 3 classes"
    assert approve_label(Decimal("1.0"), 60, False) == "Approve and split into 1 class"
    assert approve_label(None, 45, False) == "Approve and split into 1 class"
    assert approve_label(Decimal("2.5"), 60, False).endswith("3 classes")


def test_status_chip_shows_class_count():
    approved = entry(APPROVED)
    assert status_chip(approved, 1, 3) == ("Approved · 3 classes", "chip chip-ok")
    assert status_chip(approved, 1, 1)[0] == "Approved · 1 class"
    assert status_chip(approved, 1)[0] == "Approved"


def test_split_progress_labels():
    job = SimpleNamespace(kind=SPLIT_KIND, step="Writing class 2 of 3")
    steps = progress_steps(job, 1)
    assert [s[1] for s in steps] == ["done", "current", "todo"]
    assert steps[1][0] == "Writing the classes" and steps[1][2] == "Class 2 of 3"
    chapter = SimpleNamespace(kind="generate_chapter", step="Writing class 2 of 3")
    assert [s[1] for s in progress_steps(chapter, 1)] == ["current", "todo", "todo"]


def test_draft_shows_approve_and_split_button(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    store_chapter(db_session, sourced, KEY)
    page = html(signed_in, url(sourced), query_string={"chapter": KEY})
    assert "Approve and split into 4 classes" in page
    assert f'formaction="{url(sourced, "/approve")}"' in page


def test_approve_records_approval_and_queues_split(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"], Decimal("3.0"))
    version = store_chapter(db_session, sourced, KEY)
    stale = post(signed_in, sourced, "/approve", chapter=KEY, version="2")
    assert stale.status_code == 303
    assert NOT_LATEST in html(signed_in, url(sourced), query_string={"chapter": KEY})
    db_session.expire_all()
    assert (
        db_session.scalar(select(Approval).where(Approval.version_id == version.id))
        is None
    )
    assert split_jobs(db_session) == []

    r = post(signed_in, sourced, "/approve", chapter=KEY, version="1")
    assert r.status_code == 303
    db_session.expire_all()
    assert db_session.scalar(select(Approval).where(Approval.version_id == version.id))
    [job] = split_jobs(db_session)
    assert job.payload == {
        "subject_id": sourced.id,
        "chapter_key": KEY,
        "chapter_version_id": version.id,
    }
    page = html(signed_in, url(sourced), query_string={"chapter": KEY})
    assert "Splitting the chapter into classes" in page
    assert "Splitting version 1" in page
    post(signed_in, sourced, "/approve", chapter=KEY, version="1")
    assert len(split_jobs(db_session)) == 1


def test_approve_refuses_more_than_thirty_classes(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"], Decimal("40.0"))
    version = store_chapter(db_session, sourced, KEY)
    post(signed_in, sourced, "/approve", chapter=KEY, version="1")
    assert "more than 30 classes" in html(signed_in, url(sourced))
    db_session.expire_all()
    assert (
        db_session.scalar(select(Approval).where(Approval.version_id == version.id))
        is None
    )
    assert split_jobs(db_session) == []


def test_approved_without_lessons_offers_the_split_again(
    signed_in, db_session, sourced
):
    approve_overview(db_session, sourced, ["Forces"], Decimal("3.0"))
    plans.approve(db_session, store_chapter(db_session, sourced, KEY))
    db_session.commit()
    page = html(signed_in, url(sourced), query_string={"chapter": KEY})
    assert "Split into 3 classes" in page and "Approve and split" not in page


def test_approved_view_shows_classes(signed_in, db_session, sourced):
    approved_with_lessons(db_session, sourced)
    page = html(signed_in, url(sourced), query_string={"chapter": KEY})
    for text in (
        "3 h at 60 min per class = 3 classes",
        "Class 1 · 60 min",
        "Class 3 · 60 min",
        "Class title 2",
        "0–10",
        "10–30",
        "50–60",
        'class="cite',
        "Read the chapter",
        "Request changes",
        "Approved · version 1",
        "Approved · 3 classes",
    ):
        assert text in page
    assert "Next chapter" not in page
    chapter_view = html(
        signed_in, url(sourced), query_string={"chapter": KEY, "view": "chapter"}
    )
    assert "What a force is" in chapter_view and "What should change?" in chapter_view
    assert "Split into" not in chapter_view


def test_approved_view_links_next_chapter(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces", "Momentum"], Decimal("3.0"))
    chapter = store_chapter(db_session, sourced, KEY)
    plans.approve(db_session, chapter)
    db_session.commit()
    store_lessons(db_session, sourced, chapter)
    page = html(signed_in, url(sourced), query_string={"chapter": KEY})
    assert "Chapter 2 · Momentum" in page and "Write chapter draft" in page


def test_open_lesson_plan_link_when_view_exists(
    app, client, teacher, db_session, sourced
):
    sign_in(client)
    _, lessons = approved_with_lessons(db_session, sourced)
    page = html(client, url(sourced), query_string={"chapter": KEY})
    assert page.count("Open lesson plan") == 3
    first = lessons.items[0].id
    assert f'href="/subjects/{sourced.id}/today/lessons/{first}"' in page


def test_lessons_passage_panel(signed_in, db_session, sourced):
    _, lessons = approved_with_lessons(db_session, sourced, flag=True)
    flagged = lessons.sentences[0]
    cid = flagged.citations[0].id
    q = {"chapter": KEY, "version": 1, "kind": "lessons"}
    part = html(signed_in, url(sourced, f"/citations/{cid}"), query_string=q)
    assert "A force changes the motion" in part and "Ask to fix" not in part
    assert f'href="{url(sourced)}?chapter={KEY}"' in part
    page = html(signed_in, url(sourced), query_string={"chapter": KEY, "cite": cid})
    assert "A force changes the motion" in page and "Ask to fix" not in page
    bad = {**q, "kind": "overview"}
    r = signed_in.get(url(sourced, f"/citations/{cid}"), query_string=bad)
    assert r.status_code == 404


def test_request_changes_error_stays_in_chapter_view(signed_in, db_session, sourced):
    approved_with_lessons(db_session, sourced)
    r = post(signed_in, sourced, "/request", chapter=KEY, version="1", change=" ")
    assert r.status_code == 422
    page = r.get_data(as_text=True)
    assert "Say what should change" in page and "Class 1 · 60 min" not in page


def test_fix_link_keeps_chapter_view(signed_in, db_session, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    store_chapter(db_session, sourced, KEY, flag=True)
    cid = db_session.scalar(select(Sentence.id).where(Sentence.check_flag.is_(True)))
    cite = db_session.scalar(select(Citation.id).where(Citation.sentence_id == cid))
    q = {"chapter": KEY, "version": 1}
    part = html(signed_in, url(sourced, f"/citations/{cite}"), query_string=q)
    assert "view=chapter" in part and "Ask to fix this sentence" in part
