import pytest
from sqlalchemy import exists, select, text
from sqlalchemy.exc import DBAPIError

from chalkboard.ai.fake import FakeOllama
from chalkboard.ai.retrieve import retrieve
from chalkboard.ai.write import DraftSentence
from chalkboard.models import (
    Approval,
    Chunk,
    Citation,
    Job,
    PlanItem,
    PlanVersion,
    Sentence,
    Source,
    Student,
    Subject,
)
from chalkboard.plans import (
    DraftItem,
    PlanError,
    approve,
    draft_sentences,
    store_version,
)
from tests.plan_fixtures import add_source


def make_subject(db_session, teacher, name="Physics"):
    row = Subject(teacher_id=teacher.id, name=name, class_minutes=60, colour="#0E7A64")
    row.students = [Student(name="Leo Martins")]
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def subject(db_session, teacher):
    return make_subject(db_session, teacher)


def stored(db_session, subject, chunks, **kwargs):
    return store_version(
        db_session,
        subject,
        "overview",
        [DraftSentence("Light bends.", (chunks[0].id,))],
        items=[DraftItem("chapter", "Optics")],
        **kwargs,
    )


def fails(db_session, statement):
    with pytest.raises(DBAPIError, match="insert-only"), db_session.begin_nested():
        db_session.execute(text(statement))


def test_updating_version_content_fails(db_session, teacher, subject):
    chunks = add_source(db_session, subject, 1, ["Light bends.", "Lenses focus."])
    version = stored(db_session, subject, chunks)
    approve(db_session, version)
    other_job = Job(teacher_id=teacher.id, kind="x")
    db_session.add(other_job)
    db_session.flush()
    vid = version.id
    for statement in (
        f"UPDATE plan_versions SET change_request = 'x' WHERE id = {vid}",
        f"UPDATE plan_versions SET number = 7 WHERE id = {vid}",
        f"UPDATE plan_versions SET status_job_id = {other_job.id} WHERE id = {vid}",
        f"UPDATE plan_items SET title = 'x' WHERE version_id = {vid}",
        f"UPDATE sentences SET text = 'x' WHERE version_id = {vid}",
        f"UPDATE sentences SET check_flag = true WHERE version_id = {vid}",
        "UPDATE citations SET source_number = 9",
        f"UPDATE citations SET chunk_id = {chunks[1].id}",
        f"UPDATE approvals SET approved_at = approved_at + interval '1 day' WHERE version_id = {vid}",
    ):
        fails(db_session, statement)


def test_changes_only_insert_a_new_version(db_session, subject):
    chunks = add_source(db_session, subject, 1, ["Light bends."])
    v1 = stored(db_session, subject, chunks)

    def snapshot():
        return (
            db_session.execute(
                select(Sentence.text, Sentence.ordinal).where(
                    Sentence.version_id == v1.id
                )
            ).all(),
            db_session.execute(
                select(PlanItem.title).where(PlanItem.version_id == v1.id)
            ).all(),
        )

    before = snapshot()
    v2 = stored(db_session, subject, chunks, parent=v1, change_request="Shorter")
    assert (v1.number, v2.number) == (1, 2)
    assert v2.parent_version_id == v1.id
    assert len(db_session.scalars(select(PlanVersion)).all()) == 2
    assert snapshot() == before


def test_clearing_a_deleted_source_or_job_is_allowed(db_session, teacher, subject):
    chunks = add_source(db_session, subject, 4, ["Light bends."])
    job = Job(teacher_id=teacher.id, kind="x")
    db_session.add(job)
    db_session.flush()
    version = stored(db_session, subject, chunks, job_id=job.id)
    db_session.expire_all()

    db_session.delete(db_session.get(Source, chunks[0].source_id))
    db_session.flush()
    citation = db_session.scalars(select(Citation)).one()
    db_session.refresh(citation)
    assert (citation.chunk_id, citation.source_number) == (None, 4)

    db_session.delete(job)
    db_session.flush()
    db_session.refresh(version)
    assert version.status_job_id is None

    db_session.delete(subject)
    db_session.flush()
    assert db_session.scalars(select(PlanVersion)).all() == []


def test_every_stored_sentence_has_a_citation_to_an_existing_chunk(db_session, subject):
    chunks = add_source(db_session, subject, 1, ["Light bends."])
    client = FakeOllama(
        generated=[
            {
                "sentences": [
                    {"text": "Light bends.", "passages": ["P1"]},
                    {"text": "Made up.", "passages": []},
                ]
            },
            {"sentences": [{"text": "Made up.", "passages": []}]},
            {"supported": True, "reason": ""},
        ]
    )
    drafted = draft_sentences(db_session, client, subject.id, "Write", "Light bends.")
    store_version(db_session, subject, "overview", drafted)
    uncited = db_session.scalars(
        select(Sentence).where(
            ~exists(
                select(Citation.id)
                .join(Chunk, Chunk.id == Citation.chunk_id)
                .where(Citation.sentence_id == Sentence.id)
            )
        )
    ).all()
    assert uncited == []
    assert len(db_session.scalars(select(Sentence)).all()) == 1
    assert db_session.scalars(select(Citation.chunk_id)).one() == chunks[0].id


def test_database_rejects_a_sentence_without_citation(db_session, subject):
    version = PlanVersion(subject_id=subject.id, kind="overview", number=1)
    db_session.add(version)
    db_session.flush()
    db_session.add(Sentence(version_id=version.id, ordinal=0, text="Loose."))
    db_session.flush()
    with pytest.raises(DBAPIError, match="has no citation"):
        db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_store_version_refuses_uncited_or_unknown_chunks(db_session, teacher, subject):
    own = add_source(db_session, subject, 1, ["Light bends."])
    other = make_subject(db_session, teacher, "Chemistry")
    foreign = add_source(db_session, other, 1, ["Acids."])
    syllabus = add_source(db_session, subject, 2, ["Unit 1"], is_syllabus=True)
    assert own
    for ids in ((), (999999,), (foreign[0].id,), (syllabus[0].id,)):
        with pytest.raises(PlanError):
            store_version(
                db_session, subject, "overview", [DraftSentence("Nope.", ids)]
            )


def test_retrieval_never_returns_syllabus_chunks(db_session, teacher, subject):
    syllabus = add_source(db_session, subject, 1, ["Unit 1 Optics"], is_syllabus=True)
    teaching = add_source(db_session, subject, 2, ["Light bends.", "Lenses focus."])
    other = make_subject(db_session, teacher, "Chemistry")
    foreign = add_source(db_session, other, 1, ["Unit 1 Optics"])
    client = FakeOllama()
    found = retrieve(db_session, client, subject.id, "Unit 1 Optics", k=10)
    ids = {p.chunk_id for p in found}
    assert ids == {c.id for c in teaching}
    assert syllabus[0].id not in ids
    assert foreign[0].id not in ids

    db_session.delete(db_session.get(Source, teaching[0].source_id))
    db_session.flush()
    assert retrieve(db_session, client, subject.id, "Unit 1 Optics") == []


def test_approval_row_is_separate(db_session, subject):
    chunks = add_source(db_session, subject, 1, ["Light bends."])
    version = stored(db_session, subject, chunks)
    approve(db_session, version)
    assert db_session.scalars(select(Approval.version_id)).one() == version.id
