"""S06 assistant: questions about a lesson answered from the subject's sources, or labelled "Not in your sources"."""

from sqlalchemy import select

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.ai.check import check_sentences
from chalkboard.ai.retrieve import Passage, retrieve
from chalkboard.ai.write import DraftSentence, write_sentences
from chalkboard.jobs import JobContext
from chalkboard.models import ChatMessage, PlanItem, Subject

ANSWER_KIND = "assistant_answer"
QUESTION_MAX = 500
ANSWER_K = 6
STEP_ANSWER = "Writing an answer"
QUESTION_EMPTY = "Type a question."
QUESTION_TOO_LONG = f"Keep the question under {QUESTION_MAX} characters."
ANSWER_INSTRUCTION = (
    'A teacher preparing the lesson "{lesson}" for {subject} asks: {question}\n'
    "Answer in two to four short sentences, for the teacher."
)
NOT_IN_SOURCES = (
    "Your {subject} sources don't cover this. Switch to Sources + web to look it up."
)


def lesson_messages(session, item_id: int) -> list[ChatMessage]:
    return list(
        session.scalars(
            select(ChatMessage)
            .where(ChatMessage.item_id == item_id)
            .order_by(ChatMessage.id)
        )
    )


def pending_answer(session, question: ChatMessage) -> ChatMessage | None:
    return session.scalar(
        select(ChatMessage).where(ChatMessage.question_id == question.id)
    )


def ask(
    session, teacher_id: int, subject_id: int, item_id: int, text: str
) -> ChatMessage:
    """Save the teacher's question and queue its answer; the caller commits."""
    text = " ".join(text.split())
    if not text:
        raise ValueError(QUESTION_EMPTY)
    if len(text) > QUESTION_MAX:
        raise ValueError(QUESTION_TOO_LONG)
    question = ChatMessage(
        subject_id=subject_id, item_id=item_id, role="teacher", text=text
    )
    session.add(question)
    session.flush()
    job = jobs.enqueue(session, teacher_id, ANSWER_KIND, {"question_id": question.id})
    question.job_id = job.id
    return question


def answer_parts(
    client, sentences: list[DraftSentence], passages: list[Passage]
) -> list[dict]:
    """The checked sentences that survive, with names restored and their citations as [chunk id, source number]."""
    numbers = {p.chunk_id: p.source_number for p in passages}
    parts = []
    for s in sentences:
        if s.check_flag:
            continue
        cites = [[cid, numbers[cid]] for cid in s.chunk_ids if cid in numbers]
        if cites:
            parts.append({"text": client.redactor.restore(s.text), "cites": cites})
    return parts


@jobs.register(ANSWER_KIND)
def answer_question(ctx: JobContext) -> None:
    session = ctx.session
    question = session.get(ChatMessage, ctx.payload["question_id"])
    if question is None or pending_answer(session, question) is not None:
        return  # deleted, or already answered on an earlier run
    subject = session.get(Subject, question.subject_id)
    item = session.get(PlanItem, question.item_id)
    client = model_client(ctx.teacher, subject)
    try:
        ctx.progress(STEP_ANSWER, 0)
        passages = retrieve(
            session, client, subject.id, f"{question.text} {item.title}", ANSWER_K
        )
        parts = []
        if passages:
            written = write_sentences(
                client,
                ANSWER_INSTRUCTION.format(
                    lesson=item.title, subject=subject.name, question=question.text
                ),
                passages,
            )
            parts = answer_parts(
                client, check_sentences(client, written, passages), passages
            )
    finally:
        client.close()
    if parts:
        label, text = "from_sources", " ".join(p["text"] for p in parts)
    else:
        label, text = "not_in_sources", NOT_IN_SOURCES.format(subject=subject.name)
    session.add(
        ChatMessage(
            subject_id=question.subject_id,
            item_id=question.item_id,
            role="assistant",
            label=label,
            text=text,
            parts=parts,
            question_id=question.id,
        )
    )
