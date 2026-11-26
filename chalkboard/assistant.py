"""S06 assistant: questions about a lesson answered from the subject's sources, or labelled "Not in your sources"."""

from flask import current_app
from sqlalchemy import select

from chalkboard import jobs
from chalkboard.ai import TRANSPORT_KEY, model_client, websearch
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
SCOPES = ("sources", "web")
NOT_FOUND_WEB = (
    "Neither your {subject} sources nor the web search had an answer to this."
)
WEB_UNAVAILABLE = (
    "Your {subject} sources don't cover this, and web search isn't answering right now."
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
    session,
    teacher_id: int,
    subject_id: int,
    item_id: int,
    text: str,
    scope: str = "sources",
    web_enabled: bool = False,
) -> ChatMessage:
    """Save the teacher's question and queue its answer; the caller commits."""
    text = " ".join(text.split())
    if not text:
        raise ValueError(QUESTION_EMPTY)
    if len(text) > QUESTION_MAX:
        raise ValueError(QUESTION_TOO_LONG)
    if scope != "web" or not web_enabled:
        scope = "sources"
    question = ChatMessage(
        subject_id=subject_id, item_id=item_id, role="teacher", text=text, scope=scope
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


def web_links(parts: list[dict]) -> list[str]:
    """Every link of a web answer, in order, without repeats."""
    links: list[str] = []
    for part in parts:
        links.extend(url for url in part.get("links", []) if url not in links)
    return links


@jobs.register(ANSWER_KIND)
def answer_question(ctx: JobContext) -> None:
    session = ctx.session
    question = session.get(ChatMessage, ctx.payload["question_id"])
    if question is None or pending_answer(session, question) is not None:
        return  # deleted, or already answered on an earlier run
    subject = session.get(Subject, question.subject_id)
    item = session.get(PlanItem, question.item_id)
    client = model_client(ctx.teacher, subject)
    # Checked again here: the switch in Settings may have been turned off since the question was asked.
    use_web = question.scope == "web" and ctx.teacher.web_search_enabled
    web_parts: list[dict] = []
    web_failed = False
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
        if not parts and use_web:
            instruction = ANSWER_INSTRUCTION.format(
                lesson=item.title, subject=subject.name, question=question.text
            )
            try:
                results = websearch.search(
                    # Student names never leave the app.
                    client.redactor.redact(question.text),
                    current_app.extensions["chalkboard.settings"].searxng_url,
                    transport=current_app.extensions.get(TRANSPORT_KEY),
                )
                web_parts = [
                    {"text": client.redactor.restore(p["text"]), "links": p["links"]}
                    for p in websearch.write_web_answer(client, instruction, results)
                ]
            except websearch.WebSearchError:
                web_failed = True
    finally:
        client.close()
    if parts:
        label, text = "from_sources", " ".join(p["text"] for p in parts)
    elif web_parts:
        label, text = "from_web", " ".join(p["text"] for p in web_parts)
        parts = web_parts
    elif use_web:
        template = WEB_UNAVAILABLE if web_failed else NOT_FOUND_WEB
        label, text = "not_in_sources", template.format(subject=subject.name)
    else:
        label, text = "not_in_sources", NOT_IN_SOURCES.format(subject=subject.name)
    session.add(
        ChatMessage(
            subject_id=question.subject_id,
            item_id=question.item_id,
            role="assistant",
            scope="web" if use_web else "sources",
            label=label,
            text=text,
            parts=parts,
            question_id=question.id,
        )
    )
