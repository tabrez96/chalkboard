"""The course overview: the job that writes it (outline, cited sentences, check, fitted hours) and how it is queued."""

import dataclasses
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.ai.base import FormatMismatch
from chalkboard.ai.check import check_sentences
from chalkboard.ai.retrieve import TOP_K, retrieve
from chalkboard.ai.write import DraftSentence, format_passages, write_sentences
from chalkboard.hours import fit_classes, hours_for
from chalkboard.jobs import ACTIVE_STATES, JobContext
from chalkboard.models import Job, PlanVersion, Subject, SyllabusUnit
from chalkboard.plans import NO_SENTENCES, DraftItem, PlanError, store_version
from chalkboard.subjects import times_of
from chalkboard.syllabus import current_syllabus, unit_rows
from chalkboard.timetable import budget, format_duration

KIND = "generate_overview"
OUTLINE_K = 16
MAX_CHAPTERS = 30
MAX_SENTENCES = 4
MAX_WEIGHT = 200.0
CHANGE_MAX = 2000
NO_SOURCES = "Add sources first: the overview is written from them"
NO_OUTLINE = "The model didn't return a chapter outline. Try again."
PARENT_GONE = "The version this change was asked for no longer exists"
STEP_FIND = "Finding passages"
STEP_CHECK = "Checking every sentence"

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "unit": {"type": "integer"},
                    "hours": {"type": "number"},
                },
                "required": ["title"],
            },
        }
    },
    "required": ["chapters"],
}
OUTLINE_PROMPT = (
    "Plan the chapters of a course on {subject} ({grade}) taught in classes of {class_minutes} minutes.\n"
    "{budget_line}\n{units_block}\n{change_block}\n"
    "Return JSON: chapters in teaching order, each with a title, the unit number (when units are given) and an "
    "estimate of hours. Base the chapters on the passages below.\n\n{passages}"
)
CHAPTER_INSTRUCTION = 'Write 2 to 4 sentences that say what the chapter "{title}" of a course on {subject} covers.'
CHANGE_INSTRUCTION = (
    " The teacher asked for this change to the course: {change_request}"
)


class OverviewError(ValueError):
    """The message is shown to the teacher."""


@dataclass(frozen=True)
class OutlineChapter:
    title: str
    unit_number: int | None
    weight: float  # the model's hour estimate, cleaned


def parse_outline(answer: object, units: list[SyllabusUnit]) -> list[OutlineChapter]:
    entries = answer.get("chapters") if isinstance(answer, dict) else None
    if not isinstance(entries, list):
        return []
    numbers = {u.number for u in units}
    chapters = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
            continue
        title = " ".join(entry["title"].split())[:200]
        if not title:
            continue
        unit = entry.get("unit")
        unit = unit if type(unit) is int and unit in numbers else None
        hours = entry.get("hours")
        ok = type(hours) in (int, float) and 0 < hours <= MAX_WEIGHT
        chapters.append(OutlineChapter(title, unit, float(hours) if ok else 1.0))
        if len(chapters) == MAX_CHAPTERS:
            break
    if units:
        position = {u.number: u.position for u in units}
        keys = []
        for c in chapters:
            key = (
                position[c.unit_number]
                if c.unit_number is not None
                else (keys[-1] if keys else -1)
            )
            keys.append(key)
        order = sorted(range(len(chapters)), key=lambda i: keys[i])
        chapters = [chapters[i] for i in order]
    return chapters


def active_overview_job(session: Session, subject_id: int) -> Job | None:
    return session.scalar(
        select(Job)
        .where(
            Job.kind == KIND,
            Job.state.in_(ACTIVE_STATES),
            Job.payload["subject_id"].as_integer() == subject_id,
        )
        .order_by(Job.id.desc())
        .limit(1)
    )


def latest_overview_job(session: Session, subject_id: int) -> Job | None:
    """The newest overview job of the subject in any state (for the S04 progress and error states)."""
    return session.scalar(
        select(Job)
        .where(Job.kind == KIND, Job.payload["subject_id"].as_integer() == subject_id)
        .order_by(Job.id.desc())
        .limit(1)
    )


def overview_versions(session: Session, subject_id: int) -> list[PlanVersion]:
    """Newest first."""
    return list(
        session.scalars(
            select(PlanVersion)
            .where(PlanVersion.subject_id == subject_id, PlanVersion.kind == "overview")
            .order_by(PlanVersion.number.desc())
        )
    )


def latest_overview(session: Session, subject_id: int) -> PlanVersion | None:
    versions = overview_versions(session, subject_id)
    return versions[0] if versions else None


def enqueue_overview(
    session: Session,
    subject: Subject,
    parent: PlanVersion | None = None,
    change_request: str = "",
) -> Job:
    """Queue the overview job, or return the one already active for this subject; the caller commits."""
    change_request = change_request.strip()
    if parent is not None:
        if not change_request:
            raise OverviewError("Say what should change")
        if len(change_request) > CHANGE_MAX:
            raise OverviewError(f"Keep the request under {CHANGE_MAX} characters")
        if parent.subject_id != subject.id or parent.kind != "overview":
            raise OverviewError("That version belongs to another plan")
    else:
        change_request = ""
    # Serialises two clicks.
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    existing = active_overview_job(session, subject.id)
    if existing is not None:
        return existing
    return jobs.enqueue(
        session,
        subject.teacher_id,
        KIND,
        {
            "subject_id": subject.id,
            "parent_version_id": parent.id if parent is not None else None,
            "change_request": change_request,
        },
    )


def _outline_prompt(
    subject: Subject,
    units: list[SyllabusUnit],
    term_classes: int | None,
    parent: PlanVersion | None,
    change_request: str,
    passages: str,
) -> str:
    if term_classes is None:
        budget_line = "There is no timetable yet; estimate hours freely."
    else:
        duration = format_duration(term_classes * subject.class_minutes)
        budget_line = f"The term has {term_classes} classes ({duration})."
    if units:
        units_block = (
            "Follow these syllabus units in this order. Give each chapter the number of the unit it covers; "
            "a unit may span two chapters:\n"
            + "\n".join(f"Unit {u.number}: {u.title}" for u in units)
        )
    else:
        units_block = "Choose chapters that suit the passages."
    change_block = ""
    if parent is not None:
        lines = [
            f"{i}. {item.title} ({item.hours_estimate} h, unit {item.unit_number or '-'})"
            for i, item in enumerate(parent.items, 1)
        ]
        change_block = (
            "The current outline is:\n"
            + "\n".join(lines)
            + f"\n\nChange it as asked: {change_request}"
        )
    return OUTLINE_PROMPT.format(
        subject=subject.name,
        grade=subject.grade,
        class_minutes=subject.class_minutes,
        budget_line=budget_line,
        units_block=units_block,
        change_block=change_block,
        passages=passages,
    )


@jobs.register(KIND)
def generate_overview(ctx: JobContext) -> None:
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
    syllabus = current_syllabus(session, subject) if subject.board != "none" else None
    units = unit_rows(session, syllabus.id) if syllabus else []
    unit_titles = {u.number: u.title for u in units}
    b = budget(
        times_of(subject), subject.term_start, subject.term_end, subject.class_minutes
    )

    client = model_client(ctx.teacher, subject)
    try:
        ctx.progress(STEP_FIND, 0)
        query = " ".join([subject.name, subject.grade, *(u.title for u in units)])
        passages = retrieve(session, client, subject.id, query, OUTLINE_K)
        if not passages:
            raise jobs.JobFailed(NO_SOURCES)
        prompt = _outline_prompt(
            subject,
            units,
            b.classes if b else None,
            parent,
            change_request,
            format_passages(passages),
        )
        chapters = parse_outline(client.generate(prompt, OUTLINE_SCHEMA), units)
        if not chapters:
            raise FormatMismatch(NO_OUTLINE)

        n = len(chapters)
        change_instruction = (
            CHANGE_INSTRUCTION.format(change_request=change_request)
            if parent is not None
            else ""
        )
        written = []
        for i, chapter in enumerate(chapters):
            ctx.progress(f"Writing chapter {i + 1} of {n}", 10 + 60 * i // n)
            unit_title = unit_titles.get(chapter.unit_number, "")
            found = retrieve(
                session,
                client,
                subject.id,
                f"{chapter.title} {unit_title}".strip(),
                TOP_K,
            )
            sentences = write_sentences(
                client,
                CHAPTER_INSTRUCTION.format(title=chapter.title, subject=subject.name)
                + change_instruction,
                found,
            )[:MAX_SENTENCES]
            written.append((sentences, found))

        ctx.progress(STEP_CHECK, 75)
        sentences: list[DraftSentence] = []
        for i, (draft, found) in enumerate(written):
            checked = check_sentences(client, draft, found)
            sentences.extend(dataclasses.replace(s, item=i) for s in checked)
    finally:
        client.close()

    counts = fit_classes(
        [c.weight for c in chapters],
        subject.class_minutes,
        b.classes if b else None,
    )
    items = [
        DraftItem(
            "chapter", c.title, hours_for(k, subject.class_minutes), c.unit_number
        )
        for c, k in zip(chapters, counts, strict=True)
    ]
    if not sentences:
        raise jobs.JobFailed(NO_SENTENCES)
    try:
        store_version(
            session,
            subject,
            "overview",
            sentences,
            items=items,
            parent=parent,
            change_request=change_request,
            job_id=ctx.job.id,
        )
    except PlanError as exc:
        raise jobs.JobFailed(str(exc)) from exc
