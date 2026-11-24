"""Chapter drafts: the chapter list from the approved overview, added chapters, and the generate_chapter job."""

import dataclasses
import hashlib
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.ai.base import FormatMismatch
from chalkboard.ai.check import check_sentences
from chalkboard.ai.retrieve import TOP_K, retrieve
from chalkboard.ai.write import DraftSentence, format_passages, write_sentences
from chalkboard.jobs import ACTIVE_STATES, JobContext
from chalkboard.models import (
    AddedChapter,
    Approval,
    Job,
    PlanVersion,
    Subject,
)
from chalkboard.overview import NO_SOURCES, PARENT_GONE, STEP_CHECK, STEP_FIND
from chalkboard.plans import NO_SENTENCES, DraftItem, PlanError, store_version
from chalkboard.syllabus import current_syllabus, unit_rows

KIND = "generate_chapter"
SECTIONS_K = 12
MAX_SECTIONS = 8
MAX_SENTENCES = 4
TITLE_MAX = 200
NOTES_MAX = 2000
CHANGE_MAX = 2000  # same as overview.CHANGE_MAX
NOT_IN_PLAN = "This chapter is no longer in the course plan"
NO_SECTIONS = "The model didn't return the chapter's sections. Try again."
NOT_WRITTEN, DRAFT, APPROVED = "not_written", "draft", "approved"

SECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        }
    },
    "required": ["sections"],
}
SECTIONS_PROMPT = (
    'Plan the sections of the chapter "{title}" in a course on {subject} ({grade}).\n'
    "{hours_line}\n{notes_block}\n{change_block}\n"
    "Return JSON: sections in teaching order, each with a title. Base them on the passages below.\n\n{passages}"
)
SECTION_INSTRUCTION = 'Write 2 to 4 sentences for the section "{section}" of the chapter "{title}" in a course on {subject}.'
NOTES_INSTRUCTION = " The teacher wants this focus: {notes}"
CHANGE_INSTRUCTION = (
    " The teacher asked for this change to the chapter: {change_request}"
)


class ChapterError(ValueError):
    """The message is shown to the teacher."""


@dataclass(frozen=True)
class ChapterEntry:
    key: str  # PlanVersion.chapter_key
    title: str  # item 0 title of the newest version, else the overview or added title
    hours: Decimal | None  # from the overview item, or the added chapter
    unit_number: int | None  # overview item only; None for added chapters
    notes: str  # item 0 notes of the newest version, else the added chapter's notes
    added_id: int | None  # AddedChapter.id for added chapters
    status: str  # NOT_WRITTEN | DRAFT | APPROVED
    latest_version_id: int | None


def clean(text: str) -> str:
    return " ".join(text.split())


def title_key(title: str) -> str:
    return "t" + hashlib.sha256(clean(title).casefold().encode()).hexdigest()[:32]


def added_key(added_id: int) -> str:
    return f"a{added_id}"


def approved_overview(session: Session, subject_id: int) -> PlanVersion | None:
    return session.scalar(
        select(PlanVersion)
        .join(Approval, Approval.version_id == PlanVersion.id)
        .where(PlanVersion.subject_id == subject_id, PlanVersion.kind == "overview")
        .order_by(PlanVersion.number.desc())
        .limit(1)
    )


def added_chapters(session: Session, subject_id: int) -> list[AddedChapter]:
    return list(
        session.scalars(
            select(AddedChapter)
            .where(AddedChapter.subject_id == subject_id)
            .order_by(AddedChapter.id)
        )
    )


def chapter_list(session: Session, subject: Subject) -> list[ChapterEntry]:
    """The chapters of the newest approved overview, then the added ones."""
    overview = approved_overview(session, subject.id)
    if overview is None:
        return []
    rows: dict[str, tuple] = {}
    for item in overview.items:
        if item.kind != "chapter":
            continue
        key = title_key(item.title)
        if key not in rows:
            rows[key] = (
                key,
                item.title,
                item.hours_estimate,
                item.unit_number,
                "",
                None,
            )
    for a in added_chapters(session, subject.id):
        key = added_key(a.id)
        rows[key] = (key, a.title, a.hours, None, a.notes, a.id)

    newest: dict[str, PlanVersion] = {}
    for version in session.scalars(
        select(PlanVersion)
        .where(
            PlanVersion.subject_id == subject.id,
            PlanVersion.kind == "chapter",
            PlanVersion.chapter_key.in_(list(rows)),
        )
        .options(selectinload(PlanVersion.items))
        .order_by(PlanVersion.number.desc())
    ):
        newest.setdefault(version.chapter_key, version)
    approved_ids = set(
        session.scalars(
            select(Approval.version_id).where(
                Approval.version_id.in_([v.id for v in newest.values()])
            )
        )
    )

    entries = []
    for key, title, hours, unit, notes, added_id in rows.values():
        version = newest.get(key)
        if version is None:
            status = NOT_WRITTEN
        else:
            status = APPROVED if version.id in approved_ids else DRAFT
            if version.items:
                title, notes = version.items[0].title, version.items[0].notes
        entries.append(
            ChapterEntry(
                key,
                title,
                hours,
                unit,
                notes,
                added_id,
                status,
                version.id if version else None,
            )
        )
    return entries


def next_chapter(entries: list[ChapterEntry]) -> ChapterEntry | None:
    return next((e for e in entries if e.status != APPROVED), None)


def chapter_versions(session: Session, subject_id: int, key: str) -> list[PlanVersion]:
    """Newest first."""
    return list(
        session.scalars(
            select(PlanVersion)
            .where(
                PlanVersion.subject_id == subject_id,
                PlanVersion.kind == "chapter",
                PlanVersion.chapter_key == key,
            )
            .order_by(PlanVersion.number.desc())
        )
    )


def _check_title(title: str) -> str:
    title = clean(title)
    if not title:
        raise ChapterError("Give the chapter a title")
    if len(title) > TITLE_MAX:
        raise ChapterError(f"Keep the title under {TITLE_MAX} characters")
    return title


def _check_notes(notes: str) -> str:
    notes = notes.strip()
    if len(notes) > NOTES_MAX:
        raise ChapterError(f"Keep the notes under {NOTES_MAX} characters")
    return notes


def add_chapter(
    session: Session,
    subject: Subject,
    title: str,
    hours: Decimal | None,
    notes: str = "",
) -> AddedChapter:
    """Add a chapter the overview missed; the caller commits."""
    title = _check_title(title)
    if hours is not None and not Decimal("0.1") <= hours <= Decimal("999.9"):
        raise ChapterError("Hours must be between 0.1 and 999.9")
    notes = _check_notes(notes)
    row = AddedChapter(
        subject_id=subject.id,
        title=title,
        hours=hours.quantize(Decimal("0.1")) if hours is not None else None,
        notes=notes,
    )
    session.add(row)
    session.flush()
    return row


def active_chapter_job(session: Session, subject_id: int, key: str) -> Job | None:
    return session.scalar(
        select(Job)
        .where(
            Job.kind == KIND,
            Job.state.in_(ACTIVE_STATES),
            Job.payload["subject_id"].as_integer() == subject_id,
            Job.payload["chapter_key"].as_string() == key,
        )
        .order_by(Job.id.desc())
        .limit(1)
    )


def latest_chapter_job(session: Session, subject_id: int, key: str) -> Job | None:
    """The newest job for this chapter in any state (for the progress and error states)."""
    return session.scalar(
        select(Job)
        .where(
            Job.kind == KIND,
            Job.payload["subject_id"].as_integer() == subject_id,
            Job.payload["chapter_key"].as_string() == key,
        )
        .order_by(Job.id.desc())
        .limit(1)
    )


def enqueue_chapter(
    session: Session,
    subject: Subject,
    key: str,
    title: str,
    notes: str = "",
    parent: PlanVersion | None = None,
    change_request: str = "",
) -> Job:
    """Queue the chapter job, or return the one already active for this chapter; the caller commits."""
    title = _check_title(title)
    notes = _check_notes(notes)
    if key not in [e.key for e in chapter_list(session, subject)]:
        raise ChapterError(NOT_IN_PLAN)
    change_request = change_request.strip()
    if parent is not None:
        if not change_request:
            raise ChapterError("Say what should change")
        if len(change_request) > CHANGE_MAX:
            raise ChapterError(f"Keep the request under {CHANGE_MAX} characters")
        if not (
            parent.subject_id == subject.id
            and parent.kind == "chapter"
            and parent.chapter_key == key
        ):
            raise ChapterError("That version belongs to another plan")
    else:
        change_request = ""
    # Serialises two clicks.
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    existing = active_chapter_job(session, subject.id, key)
    if existing is not None:
        return existing
    return jobs.enqueue(
        session,
        subject.teacher_id,
        KIND,
        {
            "subject_id": subject.id,
            "chapter_key": key,
            "title": title,
            "notes": notes,
            "parent_version_id": parent.id if parent is not None else None,
            "change_request": change_request,
        },
    )


def parse_sections(answer: object) -> list[str]:
    entries = answer.get("sections") if isinstance(answer, dict) else None
    if not isinstance(entries, list):
        return []
    titles: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
            continue
        title = clean(entry["title"])[:TITLE_MAX]
        if not title or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        titles.append(title)
        if len(titles) == MAX_SECTIONS:
            break
    return titles


def _sections_prompt(
    subject: Subject,
    title: str,
    hours: Decimal | None,
    notes: str,
    parent: PlanVersion | None,
    change_request: str,
    passages: str,
) -> str:
    if hours is None:
        hours_line = "The chapter has no time estimate."
    else:
        hours_line = f"The chapter takes about {hours} hours in classes of {subject.class_minutes} minutes."
    change_block = ""
    if parent is not None:
        lines = [f"{i}. {item.title}" for i, item in enumerate(parent.items[1:], 1)]
        change_block = (
            "The current sections are:\n"
            + "\n".join(lines)
            + f"\n\nChange them as asked: {change_request}"
        )
    return SECTIONS_PROMPT.format(
        title=title,
        subject=subject.name,
        grade=subject.grade,
        hours_line=hours_line,
        notes_block=f"The teacher's notes on focus: {notes}" if notes else "",
        change_block=change_block,
        passages=passages,
    )


@jobs.register(KIND)
def generate_chapter(ctx: JobContext) -> None:
    session = ctx.session
    subject = session.get(Subject, ctx.payload["subject_id"])
    if subject is None:
        return
    if session.scalar(
        select(PlanVersion.id).where(PlanVersion.status_job_id == ctx.job.id)
    ):
        return
    parent = None
    parent_id = ctx.payload.get("parent_version_id")
    if parent_id:
        parent = session.get(PlanVersion, parent_id)
        if parent is None:
            raise jobs.JobFailed(PARENT_GONE)
    change_request = ctx.payload.get("change_request", "") if parent else ""
    entry = next(
        (
            e
            for e in chapter_list(session, subject)
            if e.key == ctx.payload["chapter_key"]
        ),
        None,
    )
    if entry is None:
        raise jobs.JobFailed(NOT_IN_PLAN)
    title = ctx.payload["title"]
    notes = ctx.payload.get("notes", "")
    syllabus = current_syllabus(session, subject) if subject.board != "none" else None
    units = unit_rows(session, syllabus.id) if syllabus else []
    unit_title = next(
        (u.title for u in units if u.number == entry.unit_number),
        "",
    )

    client = model_client(ctx.teacher, subject)
    try:
        ctx.progress(STEP_FIND, 0)
        query = " ".join(filter(None, [title, unit_title, notes]))
        passages = retrieve(session, client, subject.id, query, SECTIONS_K)
        if not passages:
            raise jobs.JobFailed(NO_SOURCES)
        prompt = _sections_prompt(
            subject,
            title,
            entry.hours,
            notes,
            parent,
            change_request,
            format_passages(passages),
        )
        sections = parse_sections(client.generate(prompt, SECTIONS_SCHEMA))
        if not sections:
            raise FormatMismatch(NO_SECTIONS)

        n = len(sections)
        extra = NOTES_INSTRUCTION.format(notes=notes) if notes else ""
        if parent is not None:
            extra += CHANGE_INSTRUCTION.format(change_request=change_request)
        written = []
        for i, section in enumerate(sections):
            ctx.progress(f"Writing section {i + 1} of {n}", 10 + 60 * i // n)
            found = retrieve(session, client, subject.id, f"{section} {title}", TOP_K)
            drafted = write_sentences(
                client,
                SECTION_INSTRUCTION.format(
                    section=section, title=title, subject=subject.name
                )
                + extra,
                found,
            )[:MAX_SENTENCES]
            written.append((drafted, found))

        ctx.progress(STEP_CHECK, 75)
        sentences: list[DraftSentence] = []
        for i, (draft, found) in enumerate(written):
            checked = check_sentences(client, draft, found)
            sentences.extend(dataclasses.replace(s, item=i + 1) for s in checked)
    finally:
        client.close()

    if not sentences:
        raise jobs.JobFailed(NO_SENTENCES)
    items = [
        DraftItem("chapter", title, entry.hours, entry.unit_number, notes),
        *(DraftItem("section", s) for s in sections),
    ]
    try:
        store_version(
            session,
            subject,
            "chapter",
            sentences,
            items=items,
            chapter_key=entry.key,
            parent=parent,
            change_request=change_request,
            job_id=ctx.job.id,
        )
    except PlanError as exc:
        raise jobs.JobFailed(str(exc)) from exc
