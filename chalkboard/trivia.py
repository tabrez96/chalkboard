"""S06 Trivia for class: short questions from a lesson's own passages, saved as numbered versions."""

import math
import re

from sqlalchemy import select

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.ai.check import check_sentences
from chalkboard.ai.retrieve import Passage
from chalkboard.ai.write import DraftSentence, format_passages
from chalkboard.jobs import JobContext
from chalkboard.models import (
    Chunk,
    Citation,
    Job,
    PlanItem,
    Sentence,
    Subject,
    TriviaSet,
)

WRITE_KIND = "write_trivia"
REPLACE_KIND = "replace_trivia_question"
KINDS = (WRITE_KIND, REPLACE_KIND)
COUNTS = (3, 4, 5)
DEFAULT_COUNT = 4
PASSAGE_MAX = 12
TEXT_MAX = 300
STEP_WRITE = "Writing trivia"
STEP_REPLACE = "Writing a new question"
NO_SOURCES = "This lesson cites no sources, so there is nothing to write trivia from."
NOT_ENOUGH = "Couldn't write enough questions from this lesson's sources. Try again."
NO_NEW = "Couldn't write a new question from this lesson's sources. Try again."
QUESTION_EMPTY = "Write a question and its answer."
QUESTION_TOO_LONG = (
    f"Keep the question and the answer under {TEXT_MAX} characters each."
)
# Redacted names the model could echo back; a question containing one is dropped, never shown.
PLACEHOLDER = re.compile(r"(?<!\w)(?:Student|Teacher)(?!\w)")
TRIVIA_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "passage": {"type": "string"},
                },
                "required": ["question", "answer", "passage"],
            },
        }
    },
    "required": ["questions"],
}
TRIVIA_PROMPT = (
    'Write {count} short quiz questions a teacher can ask the class right after the lesson "{lesson}" ({subject}). '
    "Each question has one short answer and is based on exactly one passage below; give that passage's label, "
    'for example "P2". Use only what the passages say.{avoid}\n\nReturn JSON.\n\n{passages}'
)
AVOID = " Do not repeat these questions:\n{questions}"


def lesson_passages(session, item_id: int) -> list[Passage]:
    """The passages the lesson's sentences cite, one per chunk, in source order."""
    rows = session.execute(
        select(
            Chunk.id,
            Citation.source_number,
            Chunk.text,
            Chunk.page_start,
            Chunk.page_end,
        )
        .join(Citation, Citation.chunk_id == Chunk.id)
        .join(Sentence, Sentence.id == Citation.sentence_id)
        .where(Sentence.item_id == item_id)
        .order_by(Citation.source_number, Chunk.id)
    )
    seen: set[int] = set()
    passages: list[Passage] = []
    for chunk_id, number, text, start, end in rows:
        if chunk_id in seen or len(passages) >= PASSAGE_MAX:
            continue
        seen.add(chunk_id)
        passages.append(
            Passage(f"P{len(passages) + 1}", chunk_id, number, text, start, end)
        )
    return passages


def clean(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def parse_questions(
    answer: object, passages: list[Passage], avoid: list[str]
) -> list[dict]:
    items = answer.get("questions") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return []
    by_label = {p.label: p for p in passages}
    seen = {q.casefold() for q in avoid}
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question, reply = clean(item.get("question")), clean(item.get("answer"))
        label = item.get("passage")
        passage = (
            by_label.get(label.strip().upper()) if isinstance(label, str) else None
        )
        if not question or not reply or passage is None:
            continue
        if len(question) > TEXT_MAX or len(reply) > TEXT_MAX:
            continue
        if PLACEHOLDER.search(question) or PLACEHOLDER.search(reply):
            continue
        if question.casefold() in seen:
            continue
        seen.add(question.casefold())
        kept.append(
            {
                "question": question,
                "answer": reply,
                "chunk_id": passage.chunk_id,
                "source_number": passage.source_number,
                "edited": False,
            }
        )
    return kept


def draft_questions(
    client,
    lesson: str,
    subject: str,
    passages: list[Passage],
    count: int,
    avoid: list[str],
) -> list[dict]:
    """Up to `count` new questions whose question and answer a passage supports."""
    prompt = TRIVIA_PROMPT.format(
        count=count,
        lesson=lesson,
        subject=subject,
        avoid=AVOID.format(questions="\n".join(avoid)) if avoid else "",
        passages=format_passages(passages),
    )
    found = parse_questions(client.generate(prompt, TRIVIA_SCHEMA), passages, avoid)
    checked = check_sentences(
        client,
        [
            DraftSentence(f"{q['question']} {q['answer']}", (q["chunk_id"],))
            for q in found
        ],
        passages,
    )
    return [q for q, c in zip(found, checked, strict=True) if not c.check_flag][:count]


def latest_set(session, item_id: int) -> TriviaSet | None:
    return session.scalar(
        select(TriviaSet)
        .where(TriviaSet.item_id == item_id)
        .order_by(TriviaSet.number.desc())
        .limit(1)
    )


class StaleSet(Exception):
    """The lesson has a newer version than the one the caller started from."""


def lock_lesson(session, subject_id: int) -> None:
    """Serialize version saves for a subject until the transaction ends."""
    session.execute(
        select(Subject.id).where(Subject.id == subject_id).with_for_update()
    )


def add_set(
    session,
    subject_id: int,
    item_id: int,
    questions: list[dict],
    job_id: int | None = None,
) -> TriviaSet:
    """Save `questions` as the lesson's next version; the caller commits."""
    latest = latest_set(session, item_id)
    row = TriviaSet(
        subject_id=subject_id,
        item_id=item_id,
        number=(latest.number if latest else 0) + 1,
        questions=questions,
        job_id=job_id,
    )
    session.add(row)
    session.flush()
    return row


def trivia_job(session, item_id: int) -> Job | None:
    return session.scalar(
        select(Job)
        .where(Job.kind.in_(KINDS), Job.payload["item_id"].as_integer() == item_id)
        .order_by(Job.id.desc())
        .limit(1)
    )


def busy(job: Job | None) -> bool:
    return job is not None and job.state in jobs.ACTIVE_STATES


def failure(job: Job, current: TriviaSet | None) -> str | None:
    """The failed job's message, unless a newer set has been saved since."""
    if job.state == "failed" and (
        current is None or job.created_at > current.created_at
    ):
        return job.error or jobs.GENERIC_FAILURE
    return None


def start_write(
    session, teacher_id: int, subject_id: int, item_id: int, count: int | None
) -> Job:
    if count not in COUNTS:
        raise ValueError(f"Choose {', '.join(map(str, COUNTS))} questions.")
    return jobs.enqueue(
        session,
        teacher_id,
        WRITE_KIND,
        {"subject_id": subject_id, "item_id": item_id, "count": count},
    )


def start_replace(
    session, teacher_id: int, subject_id: int, item_id: int, set_id: int, index: int
) -> Job:
    return jobs.enqueue(
        session,
        teacher_id,
        REPLACE_KIND,
        {
            "subject_id": subject_id,
            "item_id": item_id,
            "set_id": set_id,
            "index": index,
        },
    )


def edit_question(
    session, current: TriviaSet, index: int, question: str, answer: str
) -> TriviaSet:
    question, answer = clean(question), clean(answer)
    if not question or not answer:
        raise ValueError(QUESTION_EMPTY)
    if len(question) > TEXT_MAX or len(answer) > TEXT_MAX:
        raise ValueError(QUESTION_TOO_LONG)
    if not 0 <= index < len(current.questions):
        raise IndexError(index)
    lock_lesson(session, current.subject_id)
    if latest_set(session, current.item_id).id != current.id:
        raise StaleSet
    questions = [dict(q) for q in current.questions]
    questions[index].update(question=question, answer=answer, edited=True)
    return add_set(session, current.subject_id, current.item_id, questions)


def board_text(questions: list[dict]) -> str:
    return "\n".join(f"{i}. {q['question']}" for i, q in enumerate(questions, 1))


def minutes_for(n: int) -> int:
    return math.ceil(n * 1.25)


def already_saved(session, job_id: int) -> bool:
    return (
        session.scalar(select(TriviaSet.id).where(TriviaSet.job_id == job_id))
        is not None
    )


@jobs.register(WRITE_KIND)
def write_trivia(ctx: JobContext) -> None:
    session = ctx.session
    item = session.get(PlanItem, ctx.payload["item_id"])
    subject = session.get(Subject, ctx.payload["subject_id"])
    if item is None or subject is None or already_saved(session, ctx.job.id):
        return
    count = ctx.payload["count"]
    passages = lesson_passages(session, item.id)
    if not passages:
        raise jobs.JobFailed(NO_SOURCES)
    client = model_client(ctx.teacher, subject)
    try:
        ctx.progress(STEP_WRITE, 0)
        found = draft_questions(client, item.title, subject.name, passages, count, [])
        if len(found) < count:  # one top-up call for the missing ones
            ctx.progress(STEP_WRITE, 50)
            found += draft_questions(
                client,
                item.title,
                subject.name,
                passages,
                count - len(found),
                [q["question"] for q in found],
            )
    finally:
        client.close()
    if len(found) < count:
        raise jobs.JobFailed(NOT_ENOUGH)
    add_set(session, subject.id, item.id, found[:count], job_id=ctx.job.id)


@jobs.register(REPLACE_KIND)
def replace_trivia_question(ctx: JobContext) -> None:
    session = ctx.session
    item = session.get(PlanItem, ctx.payload["item_id"])
    subject = session.get(Subject, ctx.payload["subject_id"])
    if item is None or subject is None or already_saved(session, ctx.job.id):
        return
    # The latest set, not payload["set_id"]: an edit made while the job waited is kept.
    current = latest_set(session, item.id)
    index = ctx.payload["index"]
    if current is None or not 0 <= index < len(current.questions):
        return
    passages = lesson_passages(session, item.id)
    if not passages:
        raise jobs.JobFailed(NO_SOURCES)
    avoid = [q["question"] for q in current.questions]
    client = model_client(ctx.teacher, subject)
    try:
        ctx.progress(STEP_REPLACE, 0)
        found = draft_questions(client, item.title, subject.name, passages, 1, avoid)
        if not found:
            ctx.progress(STEP_REPLACE, 50)
            found = draft_questions(
                client, item.title, subject.name, passages, 1, avoid
            )
    finally:
        client.close()
    if not found:
        raise jobs.JobFailed(NO_NEW)
    # An edit may have been saved while the model worked: build on the newest set.
    lock_lesson(session, subject.id)
    current = latest_set(session, item.id)
    if current is None or not 0 <= index < len(current.questions):
        return
    questions = [dict(q) for q in current.questions]
    questions[index] = found[0]
    add_set(session, subject.id, item.id, questions, job_id=ctx.job.id)
