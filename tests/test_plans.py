from decimal import Decimal

import pytest
from sqlalchemy import select

from chalkboard.ai.fake import FakeOllama
from chalkboard.ai.retrieve import retrieve
from chalkboard.ai.write import DraftSentence
from chalkboard.models import Approval, PlanItem, Sentence, Subject
from chalkboard.plans import (
    DraftItem,
    PlanError,
    approve,
    draft_sentences,
    store_version,
)
from tests.plan_fixtures import add_source


@pytest.fixture
def subject(db_session, teacher):
    row = Subject(
        teacher_id=teacher.id, name="Physics", class_minutes=60, colour="#0E7A64"
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def chunks(db_session, subject):
    return add_source(db_session, subject, 1, ["Light bends.", "Lenses focus."])


def one(chunks):
    return [DraftSentence("Light bends.", (chunks[0].id,))]


def test_checker_flag_and_reason_are_stored(db_session, subject, chunks):
    client = FakeOllama(
        generated=[
            {"sentences": [{"text": "Light bends.", "passages": ["P1"]}]},
            {"supported": False, "reason": "Not in the passage"},
        ]
    )
    drafted = draft_sentences(db_session, client, subject.id, "Write", "Light bends.")
    store_version(db_session, subject, "overview", drafted)
    db_session.commit()
    stored = db_session.scalars(select(Sentence)).one()
    assert stored.check_flag is True
    assert stored.check_reason == "Source 1: Not in the passage"


def test_draft_is_empty_without_passages(db_session, subject):
    client = FakeOllama()
    assert draft_sentences(db_session, client, subject.id, "Write", "Light") == []
    assert client.calls == [["Light"]]


def test_version_numbers_per_kind_and_chapter(db_session, subject, chunks):
    def number(kind, key=None):
        return store_version(
            db_session, subject, kind, one(chunks), chapter_key=key
        ).number

    assert [number("overview"), number("overview")] == [1, 2]
    assert number("chapter", "c1") == 1
    assert number("chapter", "c2") == 1
    assert number("chapter", "c1") == 2


def test_parent_must_match(db_session, subject, chunks):
    chapter = store_version(
        db_session, subject, "chapter", one(chunks), chapter_key="c1"
    )
    with pytest.raises(PlanError):
        store_version(db_session, subject, "overview", one(chunks), parent=chapter)


def test_unknown_kind_and_empty_draft_are_refused(db_session, subject, chunks):
    with pytest.raises(PlanError):
        store_version(db_session, subject, "lesson", one(chunks))
    with pytest.raises(PlanError, match="No sentence"):
        store_version(db_session, subject, "overview", [])


def test_items_and_sentence_item_link(db_session, subject, chunks):
    items = [
        DraftItem("chapter", "Optics"),
        DraftItem("section", "Lenses", Decimal("1.5"), 2, "n"),
    ]
    version = store_version(
        db_session,
        subject,
        "overview",
        [DraftSentence("Lenses focus.", (chunks[1].id,), item=1)],
        items=items,
    )
    sentence = db_session.scalars(select(Sentence)).one()
    second = db_session.scalars(select(PlanItem).where(PlanItem.ordinal == 1)).one()
    assert sentence.item_id == second.id
    assert (second.hours_estimate, second.unit_number) == (Decimal("1.5"), 2)
    assert len(version.items) == 2
    with pytest.raises(PlanError):
        store_version(
            db_session,
            subject,
            "overview",
            [DraftSentence("X.", (chunks[0].id,), item=5)],
            items=items,
        )


def test_approve_is_idempotent(db_session, subject, chunks):
    version = store_version(db_session, subject, "overview", one(chunks))
    assert approve(db_session, version) is approve(db_session, version)
    assert len(db_session.scalars(select(Approval)).all()) == 1


def test_retrieve_labels_in_rank_order(db_session, subject, chunks):
    found = retrieve(db_session, FakeOllama(), subject.id, "Lenses focus.")
    assert [p.label for p in found] == ["P1", "P2"]
    assert found[0].chunk_id == chunks[1].id
    assert retrieve(db_session, FakeOllama(), subject.id, "  ") == []
    assert retrieve(db_session, FakeOllama(), subject.id, "x", k=0) == []


def test_lessons_version_takes_a_chapter_parent(db_session, subject, chunks):
    sentence = DraftSentence("Light bends.", (chunks[0].id,), 0, minutes=15)
    items = [DraftItem("lesson", "Class 1")]
    chapter = store_version(
        db_session,
        subject,
        "chapter",
        [DraftSentence("Light.", (chunks[0].id,))],
        chapter_key="k",
    )
    lessons = store_version(
        db_session,
        subject,
        "lessons",
        [sentence],
        items=items,
        chapter_key="k",
        parent=chapter,
    )
    db_session.commit()
    assert (lessons.kind, lessons.number, lessons.parent_version_id) == (
        "lessons",
        1,
        chapter.id,
    )
    assert [s.minutes for s in lessons.sentences] == [15]
    overview = store_version(db_session, subject, "overview", one(chunks))
    with pytest.raises(PlanError):
        store_version(
            db_session,
            subject,
            "lessons",
            [sentence],
            items=items,
            chapter_key="k",
            parent=overview,
        )
