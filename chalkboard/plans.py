"""Versioned plans: drafts are written, checked and stored as new versions; stored content is never edited."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chalkboard.ai.base import ModelClient
from chalkboard.ai.check import check_sentences
from chalkboard.ai.retrieve import TOP_K, retrieve
from chalkboard.ai.write import DraftSentence, write_sentences
from chalkboard.models import (
    PLAN_KINDS,
    Approval,
    Chunk,
    Citation,
    PlanItem,
    PlanVersion,
    Sentence,
    Source,
    Subject,
)

NO_SENTENCES = "No sentence could be tied to your sources"


class PlanError(ValueError): ...


@dataclass(frozen=True)
class DraftItem:
    kind: str
    title: str
    hours_estimate: Decimal | None = None
    unit_number: int | None = None
    notes: str = ""


def draft_sentences(
    session: Session,
    client: ModelClient,
    subject_id: int,
    instruction: str,
    query: str,
    k: int = TOP_K,
) -> list[DraftSentence]:
    passages = retrieve(session, client, subject_id, query, k)
    if not passages:
        return []
    written = write_sentences(client, instruction, passages)
    return check_sentences(client, written, passages)


def store_version(
    session: Session,
    subject: Subject,
    kind: str,
    sentences: list[DraftSentence],
    *,
    items: Sequence[DraftItem] = (),
    chapter_key: str | None = None,
    parent: PlanVersion | None = None,
    change_request: str = "",
    job_id: int | None = None,
) -> PlanVersion:
    """Add a new version; the caller commits."""
    if kind not in PLAN_KINDS:
        raise PlanError(f"Unknown plan kind {kind!r}")
    if not sentences:
        raise PlanError(NO_SENTENCES)
    for s in sentences:
        if not s.chunk_ids:
            raise PlanError("Every sentence needs a citation")
        if s.item is not None and s.item not in range(len(items)):
            raise PlanError("A sentence points at an item that does not exist")
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    ids = {c for s in sentences for c in s.chunk_ids}
    rows = session.execute(
        select(Chunk.id, Source.number, Source.subject_id, Source.is_syllabus)
        .join(Source, Source.id == Chunk.source_id)
        .where(Chunk.id.in_(ids))
    ).all()
    number_by_chunk = {r.id: r.number for r in rows}
    if any(r.subject_id != subject.id or r.is_syllabus for r in rows) or len(
        number_by_chunk
    ) != len(ids):
        raise PlanError(
            "A sentence cites a passage that is not one of the subject's sources"
        )
    parent_kind = "chapter" if kind == "lessons" else kind
    if parent is not None and (
        parent.subject_id,
        parent.kind,
        parent.chapter_key,
    ) != (subject.id, parent_kind, chapter_key):
        raise PlanError("The parent version belongs to a different plan")
    number = (
        session.scalar(
            select(func.max(PlanVersion.number)).where(
                PlanVersion.subject_id == subject.id,
                PlanVersion.kind == kind,
                PlanVersion.chapter_key.is_not_distinct_from(chapter_key),
            )
        )
        or 0
    ) + 1
    version = PlanVersion(
        subject_id=subject.id,
        kind=kind,
        chapter_key=chapter_key,
        number=number,
        parent_version_id=parent.id if parent is not None else None,
        change_request=change_request,
        status_job_id=job_id,
        items=[
            PlanItem(
                ordinal=i,
                kind=item.kind,
                title=item.title,
                hours_estimate=item.hours_estimate,
                unit_number=item.unit_number,
                notes=item.notes,
            )
            for i, item in enumerate(items)
        ],
    )
    session.add(version)
    session.flush()
    for i, s in enumerate(sentences):
        session.add(
            Sentence(
                version_id=version.id,
                item_id=version.items[s.item].id if s.item is not None else None,
                ordinal=i,
                minutes=s.minutes,
                text=s.text,
                check_flag=s.check_flag,
                check_reason=s.check_reason,
                citations=[
                    Citation(chunk_id=c, source_number=number_by_chunk[c])
                    for c in s.chunk_ids
                ],
            )
        )
    session.flush()
    return version


def approve(session: Session, version: PlanVersion) -> Approval:
    """Record the teacher's approval of a version; the caller commits."""
    existing = session.scalar(select(Approval).where(Approval.version_id == version.id))
    if existing is not None:
        return existing
    approval = Approval(version_id=version.id)
    session.add(approval)
    session.flush()
    return approval
