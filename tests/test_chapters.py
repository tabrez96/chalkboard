from decimal import Decimal

import pytest
from sqlalchemy import select

from chalkboard import chapters, jobs, plans
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.ai.write import DraftSentence
from chalkboard.chapters import (
    ChapterError,
    add_chapter,
    added_key,
    chapter_list,
    chapter_versions,
    enqueue_chapter,
    next_chapter,
    parse_sections,
    title_key,
)
from chalkboard.models import Chunk, Source, Subject, SyllabusUnit
from chalkboard.plans import DraftItem, store_version
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


def chunk_id(db_session, subject):
    return db_session.scalar(
        select(Chunk.id)
        .join(Source, Chunk.source_id == Source.id)
        .where(Source.subject_id == subject.id, Source.is_syllabus.is_(False))
        .limit(1)
    )


def store_overview(db_session, subject, titles, hours=Decimal("2.0")):
    cid = chunk_id(db_session, subject)
    items = [DraftItem("chapter", t, hours, 1) for t in titles]
    sentences = [
        DraftSentence(f"{t} is covered.", (cid,), i) for i, t in enumerate(titles)
    ]
    return store_version(db_session, subject, "overview", sentences, items=items)


def approve_overview(db_session, subject, titles, hours=Decimal("2.0")):
    version = store_overview(db_session, subject, titles, hours)
    plans.approve(db_session, version)
    db_session.commit()
    return version


def store_chapter(db_session, subject, key, title="Ch"):
    cid = chunk_id(db_session, subject)
    return store_version(
        db_session,
        subject,
        "chapter",
        [DraftSentence("A sentence.", (cid,), 1)],
        items=[DraftItem("chapter", title), DraftItem("section", "S")],
        chapter_key=key,
    )


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


def run_chapter(app, db_session, subject, key, title, notes="", parent=None, change=""):
    job = enqueue_chapter(db_session, subject, key, title, notes, parent, change)
    db_session.commit()
    run_all(app)
    db_session.expire_all()
    return job


def sections_prompt(server):
    return next(c[0] for c in server.calls if c[0].startswith("Plan the sections"))


def snapshot(version):
    return (
        [
            (i.kind, i.title, i.hours_estimate, i.unit_number, i.notes)
            for i in version.items
        ],
        [s.text for s in version.sentences],
    )


def test_title_key_normalises():
    key = title_key(" Laws  of Motion")
    assert key == title_key("laws of motion")
    assert key.startswith("t") and len(key) == 33
    assert added_key(7) == "a7"


def test_list_empty_without_approved_overview(db_session, sourced):
    store_overview(db_session, sourced, ["A"])
    add_chapter(db_session, sourced, "Extra", None)
    db_session.commit()
    assert chapter_list(db_session, sourced) == []


def test_list_uses_newest_approved_overview_then_added(db_session, sourced):
    approve_overview(db_session, sourced, ["A", "B"], Decimal("3.0"))
    store_overview(db_session, sourced, ["X"])
    added = add_chapter(db_session, sourced, "Extra", Decimal("1.5"), "notes")
    db_session.commit()
    entries = chapter_list(db_session, sourced)
    assert [e.title for e in entries] == ["A", "B", "Extra"]
    assert [e.key for e in entries] == [title_key("A"), title_key("B"), f"a{added.id}"]
    assert [e.hours for e in entries] == [
        Decimal("3.0"),
        Decimal("3.0"),
        Decimal("1.5"),
    ]
    assert [e.unit_number for e in entries] == [1, 1, None]
    assert entries[2].notes == "notes" and entries[2].added_id == added.id


def test_list_drops_repeated_title(db_session, sourced):
    approve_overview(db_session, sourced, ["A", "a "])
    assert [e.title for e in chapter_list(db_session, sourced)] == ["A"]


def test_status_and_next_chapter(db_session, sourced):
    approve_overview(db_session, sourced, ["A", "B"])
    added = add_chapter(db_session, sourced, "Extra", None)
    db_session.commit()
    entries = chapter_list(db_session, sourced)
    assert {e.status for e in entries} == {chapters.NOT_WRITTEN}
    assert next_chapter(entries) == entries[0]

    a = store_chapter(db_session, sourced, title_key("A"))
    db_session.commit()
    entries = chapter_list(db_session, sourced)
    assert entries[0].status == chapters.DRAFT
    assert entries[0].latest_version_id == a.id

    plans.approve(db_session, a)
    db_session.commit()
    entries = chapter_list(db_session, sourced)
    assert entries[0].status == chapters.APPROVED
    assert next_chapter(entries).key == title_key("B")

    plans.approve(db_session, store_chapter(db_session, sourced, title_key("B")))
    db_session.commit()
    assert next_chapter(chapter_list(db_session, sourced)).key == f"a{added.id}"

    plans.approve(db_session, store_chapter(db_session, sourced, f"a{added.id}"))
    db_session.commit()
    assert next_chapter(chapter_list(db_session, sourced)) is None

    # a newer unapproved version puts an approved chapter back to draft
    store_chapter(db_session, sourced, title_key("A"))
    db_session.commit()
    assert chapter_list(db_session, sourced)[0].status == chapters.DRAFT


def test_add_chapter_validates(db_session, subject):
    for title, hours, notes in [
        ("  ", None, ""),
        ("x" * 201, None, ""),
        ("T", Decimal(0), ""),
        ("T", Decimal(1000), ""),
        ("T", None, "n" * 2001),
    ]:
        with pytest.raises(ChapterError):
            add_chapter(db_session, subject, title, hours, notes)
    row = add_chapter(db_session, subject, "  Waves   and sound ", Decimal("1.26"))
    assert (row.title, row.hours) == ("Waves and sound", Decimal("1.3"))


def test_generate_chapter_stores_version_one(app, db_session, server, sourced):
    approve_overview(db_session, sourced, ["Forces"], Decimal("2.0"))
    key = title_key("Forces")
    server.generated = script()
    job = run_chapter(app, db_session, sourced, key, "Forces", "Mind friction")
    assert job.state == "done"
    [v] = chapter_versions(db_session, sourced.id, key)
    assert (v.kind, v.number, v.chapter_key, v.parent_version_id) == (
        "chapter",
        1,
        key,
        None,
    )
    first, s1, s2 = v.items
    assert (first.kind, first.title, first.hours_estimate, first.unit_number) == (
        "chapter",
        "Forces",
        Decimal("2.0"),
        1,
    )
    assert first.notes == "Mind friction"
    assert [(s1.kind, s1.title), (s2.kind, s2.title)] == [
        ("section", "S1"),
        ("section", "S2"),
    ]
    assert len(v.sentences) == 4
    assert all(s.item_id in (s1.id, s2.id) for s in v.sentences)


def test_rename_and_notes_kept(app, db_session, server, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    key = title_key("Forces")
    server.generated = script()
    run_chapter(app, db_session, sourced, key, "Forces, renamed", "Focus on friction")
    [v] = chapter_versions(db_session, sourced.id, key)
    assert v.items[0].title == "Forces, renamed"
    assert v.items[0].notes == "Focus on friction"
    [entry] = chapter_list(db_session, sourced)
    assert (entry.key, entry.title, entry.notes) == (
        key,
        "Forces, renamed",
        "Focus on friction",
    )
    assert "Focus on friction" in sections_prompt(server)


def test_every_stored_sentence_has_a_citation(app, db_session, server, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    key = title_key("Forces")
    mixed = {
        "sentences": [
            {"text": "Cited.", "passages": ["P1"]},
            {"text": "Uncited.", "passages": []},
        ]
    }
    retry = {"sentences": [{"text": "Uncited.", "passages": []}]}
    server.generated = [
        {"sections": [{"title": "S1"}]},
        mixed,
        retry,
        SUPPORTED,
    ]
    run_chapter(app, db_session, sourced, key, "Forces")
    [v] = chapter_versions(db_session, sourced.id, key)
    assert [s.text for s in v.sentences] == ["Cited."]
    for sentence in v.sentences:
        assert sentence.citations
        for citation in sentence.citations:
            source = db_session.scalar(
                select(Source)
                .join(Chunk, Chunk.source_id == Source.id)
                .where(Chunk.id == citation.chunk_id)
            )
            assert source.subject_id == sourced.id and not source.is_syllabus


def test_request_changes_writes_next_version(app, db_session, server, sourced):
    approve_overview(db_session, sourced, ["Forces"])
    key = title_key("Forces")
    server.generated = script()
    run_chapter(app, db_session, sourced, key, "Forces")
    [v1] = chapter_versions(db_session, sourced.id, key)
    before = snapshot(v1)
    server.generated = script()
    server.calls.clear()
    run_chapter(app, db_session, sourced, key, "Forces", parent=v1, change="Shorter")
    v2, v1 = chapter_versions(db_session, sourced.id, key)
    assert (v2.number, v2.parent_version_id, v2.change_request) == (2, v1.id, "Shorter")
    assert snapshot(v1) == before
    prompt = sections_prompt(server)
    assert "1. S1" in prompt and "2. S2" in prompt and "Shorter" in prompt


def test_enqueue_rejects_bad_input(db_session, sourced):
    overview = approve_overview(db_session, sourced, ["A", "B"])
    key = title_key("A")
    chapter = store_chapter(db_session, sourced, key)
    other = store_chapter(db_session, sourced, title_key("B"))
    db_session.commit()
    with pytest.raises(ChapterError, match="no longer"):
        enqueue_chapter(db_session, sourced, "tunknown", "A")
    with pytest.raises(ChapterError):
        enqueue_chapter(db_session, sourced, key, "  ")
    with pytest.raises(ChapterError, match="Say what"):
        enqueue_chapter(db_session, sourced, key, "A", parent=chapter)
    with pytest.raises(ChapterError, match="another plan"):
        enqueue_chapter(db_session, sourced, key, "A", parent=other, change_request="x")
    with pytest.raises(ChapterError, match="another plan"):
        enqueue_chapter(
            db_session, sourced, key, "A", parent=overview, change_request="x"
        )
    with pytest.raises(ChapterError):
        enqueue_chapter(db_session, sourced, key, "A", notes="n" * 2001)
    with pytest.raises(ChapterError, match="request"):
        enqueue_chapter(
            db_session, sourced, key, "A", parent=chapter, change_request="x" * 2001
        )


def test_enqueue_without_approved_overview(db_session, sourced):
    with pytest.raises(ChapterError, match="no longer"):
        enqueue_chapter(db_session, sourced, title_key("A"), "A")


def test_enqueue_returns_active_job_per_chapter(db_session, sourced):
    approve_overview(db_session, sourced, ["A", "B"])
    first = enqueue_chapter(db_session, sourced, title_key("A"), "A")
    db_session.commit()
    assert enqueue_chapter(db_session, sourced, title_key("A"), "A").id == first.id
    assert enqueue_chapter(db_session, sourced, title_key("B"), "B").id != first.id
    assert (
        chapters.active_chapter_job(db_session, sourced.id, title_key("A")).id
        == first.id
    )
    assert (
        chapters.latest_chapter_job(db_session, sourced.id, title_key("A")).id
        == first.id
    )


def test_no_sections_fails(app, db_session, server, sourced):
    approve_overview(db_session, sourced, ["A"])
    server.generated = [{"sections": []}] * 5
    job = run_chapter(app, db_session, sourced, title_key("A"), "A")
    assert job.state in ("queued", "failed")
    assert job.error == chapters.NO_SECTIONS
    assert chapter_versions(db_session, sourced.id, title_key("A")) == []


def test_no_sources_fails(app, db_session, server, sourced, monkeypatch):
    approve_overview(db_session, sourced, ["A"])
    # An approved overview needs a teaching source, so empty retrieval stands in for none.
    monkeypatch.setattr(chapters, "retrieve", lambda *args, **kwargs: [])
    job = run_chapter(app, db_session, sourced, title_key("A"), "A")
    assert (job.state, job.error) == ("failed", chapters.NO_SOURCES)
    assert chapter_versions(db_session, sourced.id, title_key("A")) == []


def test_parent_gone_and_missing_chapter_fail(app, db_session, server, sourced):
    approve_overview(db_session, sourced, ["A"])
    key = title_key("A")
    job = jobs.enqueue(
        db_session,
        sourced.teacher_id,
        chapters.KIND,
        {
            "subject_id": sourced.id,
            "chapter_key": key,
            "title": "A",
            "notes": "",
            "parent_version_id": 999999,
            "change_request": "x",
        },
    )
    gone = jobs.enqueue(
        db_session,
        sourced.teacher_id,
        chapters.KIND,
        {
            "subject_id": sourced.id,
            "chapter_key": "tmissing",
            "title": "A",
            "notes": "",
            "parent_version_id": None,
            "change_request": "",
        },
    )
    db_session.commit()
    run_all(app)
    db_session.expire_all()
    assert job.error == chapters.PARENT_GONE
    assert gone.error == chapters.NOT_IN_PLAN


def test_parse_sections_cleans():
    answer = {
        "sections": [
            {"title": "  One   thing "},
            "junk",
            {"title": 5},
            {"title": "   "},
            {"title": "one THING"},
            *({"title": f"S{i}"} for i in range(20)),
        ]
    }
    result = parse_sections(answer)
    assert result[0] == "One thing"
    assert len(result) == chapters.MAX_SECTIONS
    assert parse_sections({"sections": "x"}) == []
    assert parse_sections([]) == []
