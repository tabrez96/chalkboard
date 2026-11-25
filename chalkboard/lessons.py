"""Lesson plans: split an approved chapter into classes with timed, cited steps (the split_chapter job)."""

import dataclasses
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.ai.check import check_sentences
from chalkboard.ai.retrieve import TOP_K, retrieve
from chalkboard.ai.write import DraftSentence, format_passages
from chalkboard.chapters import (
    NOT_IN_PLAN,
    SPLIT_KIND,
    TITLE_MAX,
    ChapterError,
    chapter_list,
    clean,
)
from chalkboard.hours import fit_minutes, lesson_minutes
from chalkboard.jobs import JobContext
from chalkboard.models import Approval, Job, PlanVersion, Subject
from chalkboard.overview import NO_SOURCES, PARENT_GONE, STEP_CHECK, STEP_FIND
from chalkboard.plans import DraftItem, PlanError, store_version

MAX_CLASSES = 30
MAX_STEPS = 6
CLASSES_K = 12
TOO_MANY = f"This chapter would make more than {MAX_CLASSES} classes. Give it fewer hours in the course overview, or make the classes longer."
NOT_APPROVED = "Approve the chapter before splitting it"
NO_STEPS = "The model didn't return timed steps for class {n}. Try again."

CLASSES_SCHEMA = {
    "type": "object",
    "properties": {
        "classes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        }
    },
    "required": ["classes"],
}
CLASSES_PROMPT = (
    'Split the chapter "{title}" in a course on {subject} ({grade}) into {n} classes of {minutes_list} minutes.\n'
    "The chapter's sections are:\n{sections}\n\n"
    "Return JSON: exactly {n} classes in teaching order, each with a short title. Base them on the passages below.\n\n{passages}"
)
STEPS_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "minutes": {"type": "integer"},
                    "passages": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "minutes", "passages"],
            },
        }
    },
    "required": ["steps"],
}
STEPS_PROMPT = (
    'Plan class {i} of {n} of the chapter "{title}" in a course on {subject}: "{class_title}", {minutes} minutes.\n'
    "Return JSON: 2 to {max_steps} timed steps in teaching order. Each step is one sentence saying what the class does "
    'and covers, its minutes, and the labels of the passages it is based on, for example ["P2"]. Every step must cite '
    "at least one passage. Write only from the passages below.\n\n{passages}"
)


def split_minutes(hours: Decimal | None, class_minutes: int) -> list[int]:
    """Minutes of each class; a chapter without hours becomes one class."""
    if hours:
        return lesson_minutes(hours, class_minutes)
    return [class_minutes]


def active_split_job(session: Session, version: PlanVersion) -> Job | None:
    """The newest queued or running split of this chapter version."""
    return session.scalar(
        select(Job)
        .where(
            Job.kind == SPLIT_KIND,
            Job.state.in_(jobs.ACTIVE_STATES),
            Job.payload["chapter_version_id"].as_integer() == version.id,
        )
        .order_by(Job.id.desc())
        .limit(1)
    )


def enqueue_split(session: Session, subject: Subject, version: PlanVersion) -> Job:
    """Queue the split job, or return the one already active for this version; the caller commits."""
    if version.subject_id != subject.id or version.kind != "chapter":
        raise ChapterError("That version belongs to another plan")
    # Serialises two clicks.
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    existing = active_split_job(session, version)
    if existing is not None:
        return existing
    entry = next(
        (e for e in chapter_list(session, subject) if e.key == version.chapter_key),
        None,
    )
    if entry is None:
        raise ChapterError(NOT_IN_PLAN)
    if len(split_minutes(entry.hours, subject.class_minutes)) > MAX_CLASSES:
        raise ChapterError(TOO_MANY)
    return jobs.enqueue(
        session,
        subject.teacher_id,
        SPLIT_KIND,
        {
            "subject_id": subject.id,
            "chapter_key": version.chapter_key,
            "chapter_version_id": version.id,
        },
    )


def lessons_for(
    session: Session, chapter_version_ids: list[int]
) -> dict[int, PlanVersion]:
    """The newest lessons version of each chapter version, keyed by the chapter version's id."""
    if not chapter_version_ids:
        return {}
    found: dict[int, PlanVersion] = {}
    for version in session.scalars(
        select(PlanVersion)
        .where(
            PlanVersion.kind == "lessons",
            PlanVersion.parent_version_id.in_(chapter_version_ids),
        )
        .options(selectinload(PlanVersion.items))
        .order_by(PlanVersion.number)
    ):
        found[version.parent_version_id] = version
    return found


def parse_class_titles(answer: object, n: int, chapter_title: str) -> list[str]:
    entries = answer.get("classes") if isinstance(answer, dict) else None
    titles: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
            continue
        title = clean(entry["title"])[:TITLE_MAX]
        if title:
            titles.append(title)
        if len(titles) == n:
            break
    while len(titles) < n:
        titles.append(f"{chapter_title}, part {len(titles) + 1}"[:TITLE_MAX])
    return titles


def parse_steps(answer: object, labels: dict[str, int]) -> list[DraftSentence]:
    entries = answer.get("steps") if isinstance(answer, dict) else None
    out = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        text = clean(entry["text"])
        if not text:
            continue
        given = entry.get("passages")
        ids: list[int] = []
        for label in given if isinstance(given, list) else []:
            chunk_id = (
                labels.get(label.strip().upper()) if isinstance(label, str) else None
            )
            if chunk_id is not None and chunk_id not in ids:
                ids.append(chunk_id)
        if not ids:
            continue
        minutes = entry.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool):
            minutes = 0
        out.append(DraftSentence(text, tuple(ids), minutes=minutes))
    return out


@jobs.register(SPLIT_KIND)
def split_chapter(ctx: JobContext) -> None:
    session = ctx.session
    subject = session.get(Subject, ctx.payload["subject_id"])
    if subject is None:
        return
    if session.scalar(
        select(PlanVersion.id).where(PlanVersion.status_job_id == ctx.job.id)
    ):
        return
    chapter = session.get(PlanVersion, ctx.payload["chapter_version_id"])
    if chapter is None:
        raise jobs.JobFailed(PARENT_GONE)
    if not session.scalar(select(Approval.id).where(Approval.version_id == chapter.id)):
        raise jobs.JobFailed(NOT_APPROVED)
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
    title = chapter.items[0].title
    section_titles = [item.title for item in chapter.items[1:]]
    minutes = split_minutes(entry.hours, subject.class_minutes)
    n = len(minutes)
    if n > MAX_CLASSES:
        raise jobs.JobFailed(TOO_MANY)

    client = model_client(ctx.teacher, subject)
    try:
        ctx.progress(STEP_FIND, 0)
        passages = retrieve(
            session, client, subject.id, " ".join([title, *section_titles]), CLASSES_K
        )
        if not passages:
            raise jobs.JobFailed(NO_SOURCES)
        prompt = CLASSES_PROMPT.format(
            title=title,
            subject=subject.name,
            grade=subject.grade,
            n=n,
            minutes_list=", ".join(map(str, minutes)),
            sections="\n".join(f"- {t}" for t in section_titles),
            passages=format_passages(passages),
        )
        titles = parse_class_titles(client.generate(prompt, CLASSES_SCHEMA), n, title)

        written = []
        for i in range(n):
            ctx.progress(f"Writing class {i + 1} of {n}", 10 + 60 * i // n)
            found = retrieve(session, client, subject.id, f"{titles[i]} {title}", TOP_K)
            steps: list[DraftSentence] = []
            if found:
                answer = client.generate(
                    STEPS_PROMPT.format(
                        i=i + 1,
                        n=n,
                        title=title,
                        subject=subject.name,
                        class_title=titles[i],
                        minutes=minutes[i],
                        max_steps=min(MAX_STEPS, minutes[i]),
                        passages=format_passages(found),
                    ),
                    STEPS_SCHEMA,
                )
                steps = parse_steps(answer, {p.label: p.chunk_id for p in found})[
                    : min(MAX_STEPS, minutes[i])
                ]
            if not steps:
                raise jobs.JobFailed(NO_STEPS.format(n=i + 1))
            written.append((steps, found))

        sentences: list[DraftSentence] = []
        for i, (steps, found) in enumerate(written):
            # One heartbeat per class: each check is a model call.
            ctx.progress(STEP_CHECK, 75 + 20 * i // len(written))
            checked = check_sentences(client, steps, found)
            fitted = fit_minutes([s.minutes or 0 for s in checked], minutes[i])
            sentences.extend(
                dataclasses.replace(s, item=i, minutes=m)
                for s, m in zip(checked, fitted, strict=True)
            )
    finally:
        client.close()

    try:
        store_version(
            session,
            subject,
            "lessons",
            sentences,
            items=[DraftItem("lesson", t) for t in titles],
            chapter_key=chapter.chapter_key,
            parent=chapter,
            job_id=ctx.job.id,
        )
    except PlanError as exc:
        raise jobs.JobFailed(str(exc)) from exc
