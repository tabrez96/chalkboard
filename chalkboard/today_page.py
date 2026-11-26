"""S06 Today: the lesson placed on today's date, its steps and sources, and the empty states."""

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from chalkboard import assistant, trivia
from chalkboard.chapters_page import time_ranges
from chalkboard.db import get_session
from chalkboard.lessons import placement, term_slots
from chalkboard.models import (
    ChatMessage,
    Chunk,
    Citation,
    Job,
    PlanItem,
    PlanVersion,
    Sentence,
    Source,
)
from chalkboard.overview_page import (
    amber_citation_ids,
    checked_post,
    load_passage,
    ollama_down,
)
from chalkboard.placement import (
    Placed,
    class_line,
    day_view,
    lesson_neighbours,
    long_date,
    pages_short,
    short_date,
    time_range,
)
from chalkboard.subjects import own_subject

TRIVIA_CHANGED = "This trivia changed since you opened it. Edit the newest version."

bp = Blueprint("today", __name__, url_prefix="/subjects/<int:subject_id>/today")

SOURCE_DELETED = "Source deleted"


def now() -> datetime:
    return datetime.now()


def find_lesson(placed: list[Placed], item_id: int) -> Placed:
    found = next((p for p in placed if p.lesson.item_id == item_id), None)
    if found is None:
        abort(404)
    return found


def lesson_version(session, item_id: int) -> PlanVersion:
    item = session.get(PlanItem, item_id)
    return session.scalars(
        select(PlanVersion)
        .where(PlanVersion.id == item.version_id)
        .options(selectinload(PlanVersion.sentences).selectinload(Sentence.citations))
    ).one()


def source_rows(session, steps: list[Sentence]) -> list[dict]:
    """One row per source the lesson cites: number, name, page range and a citation that opens it."""
    ids = [c.id for s in steps for c in s.citations]
    if not ids:
        return []
    rows: dict[int, dict] = {}
    pages: dict[int, list[tuple[int, int]]] = {}
    for cite_id, number, name, start, end in session.execute(
        select(
            Citation.id,
            Citation.source_number,
            Source.name,
            Chunk.page_start,
            Chunk.page_end,
        )
        .outerjoin(Chunk, Chunk.id == Citation.chunk_id)
        .outerjoin(Source, Source.id == Chunk.source_id)
        .where(Citation.id.in_(ids))
        .order_by(Citation.source_number, Citation.id)
    ):
        rows.setdefault(
            number,
            {"number": number, "name": name or SOURCE_DELETED, "cite_id": cite_id},
        )
        if start is not None:
            pages.setdefault(number, []).append((start, end or start))
    for number, row in rows.items():
        spans = pages.get(number)
        row["pages"] = (
            pages_short(min(s for s, _ in spans), max(e for _, e in spans))
            if spans
            else ""
        )
    return [rows[n] for n in sorted(rows)]


def chat_context(session, item_id: int, **extra) -> dict:
    messages = assistant.lesson_messages(session, item_id)
    return {
        "messages": messages,
        # Questions without an answer yet get typing dots.
        "answered": {m.question_id for m in messages if m.question_id},
        "jobs": {
            j.id: j
            for j in session.scalars(
                select(Job).where(Job.id.in_([m.job_id for m in messages if m.job_id]))
            )
        },
        "ollama_down": ollama_down(),
        "question_max": assistant.QUESTION_MAX,
        "chat_error": None,
        "scope": "sources",
        "web_enabled": g.teacher.web_search_enabled,
        **extra,
    }


def chat_partial(subject, item_id: int, **extra):
    return render_template(
        "today/_assistant.html",
        subject=subject,
        item_id=item_id,
        **chat_context(get_session(), item_id, **extra),
    )


@bp.app_template_global()
def web_links(parts):
    return assistant.web_links(parts)


@bp.app_template_filter("web_link")
def web_link(url: str) -> str:
    """'https://en.wikipedia.org/wiki/Usain_Bolt' -> 'en.wikipedia.org/wiki/Usain_Bolt'."""
    parts = urlsplit(url)
    shown = parts.netloc + parts.path
    if parts.query:
        shown += "?" + parts.query
    return shown.rstrip("/")


def is_htmx() -> bool:
    return request.headers.get("HX-Request") == "true"


def lesson_context(subject, placed: list[Placed], current: Placed, cite_id) -> dict:
    session = get_session()
    item_id = current.lesson.item_id
    item = session.get(PlanItem, item_id)
    version = lesson_version(session, item_id)
    sentences = sorted(
        (s for s in version.sentences if s.item_id == item_id), key=lambda s: s.ordinal
    )
    ranges = time_ranges([s.minutes or 0 for s in sentences])
    before, after = lesson_neighbours(placed, item_id)

    def lesson_url(p: Placed | None) -> str | None:
        if p is None:
            return None
        return url_for("today.lesson", subject_id=subject.id, item_id=p.lesson.item_id)

    passage = load_passage(session, version, cite_id) if cite_id else None
    if passage is not None and passage["sentence"].item_id != item_id:
        passage = None
    when = current.when
    over = class_over(current)
    return {
        **chat_context(session, item_id),
        **trivia_context(session, item_id, sentences),
        "class_over": over,
        "show_trivia": over
        or request.args.get("trivia") == "1"
        or request.args.get("edit") is not None,
        "trivia_url": url_for(
            "today.lesson", subject_id=subject.id, item_id=item_id, trivia=1
        )
        + "#trivia",
        "mode": "lesson",
        "subject": subject,
        "active": "today.show",
        "title": item.title,
        "item_id": item_id,
        "header_line": " · ".join(
            [
                subject.name,
                long_date(when) if when else "No class date",
                class_line(current.lesson),
            ]
        ),
        "time": time_range(when, current.lesson.minutes) if when else "",
        "steps": list(zip(ranges, sentences, strict=True)),
        "amber_ids": amber_citation_ids(sentences),
        "sources": source_rows(session, sentences),
        "prev_url": lesson_url(before),
        "next_url": lesson_url(after),
        "passage": passage,
        "version": version,
    }


def class_over(current: Placed) -> bool:
    return current.when is not None and now() >= current.when + timedelta(
        minutes=current.lesson.minutes
    )


def trivia_context(session, item_id: int, sentences: list[Sentence]) -> dict:
    current = trivia.latest_set(session, item_id)
    job = trivia.trivia_job(session, item_id)
    questions = current.questions if current else []
    return {
        "trivia": current,
        "trivia_job": job if trivia.busy(job) else None,
        "trivia_error": trivia.failure(job, current) if job else None,
        # chunk id -> a lesson citation id, so a trivia number opens the existing passage panel
        "trivia_cites": {
            c.chunk_id: c.id for s in sentences for c in s.citations if c.chunk_id
        },
        "trivia_board": trivia.board_text(questions),
        "trivia_minutes": trivia.minutes_for(len(questions)),
        "trivia_counts": trivia.COUNTS,
        "trivia_default": trivia.DEFAULT_COUNT,
        "trivia_editing": request.args.get("edit", type=int),
    }


def trivia_back(subject, item_id: int, **args):
    return redirect(
        url_for(
            "today.lesson", subject_id=subject.id, item_id=item_id, trivia=1, **args
        )
        + "#trivia",
        303,
    )


def render_lesson(subject, placed: list[Placed], current: Placed):
    cite = request.args.get("cite", type=int)
    return render_template(
        "today/index.html", **lesson_context(subject, placed, current, cite)
    )


@bp.get("")
def show(subject_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    placed = placement(session, subject)
    today = now().date()
    view = day_view(placed, term_slots(subject), today)
    if view.state == "lesson":
        return render_lesson(subject, placed, view.today)
    return render_template(
        "today/index.html",
        mode=view.state,
        view=view,
        subject=subject,
        active="today.show",
        today=today,
        long_date=long_date,
        short_date=short_date,
        class_line=class_line,
    )


@bp.get("/lessons/<int:item_id>")
def lesson(subject_id: int, item_id: int):
    subject = own_subject(subject_id)
    placed = placement(get_session(), subject)
    return render_lesson(subject, placed, find_lesson(placed, item_id))


@bp.get("/lessons/<int:item_id>/citations/<int:citation_id>")
def passage(subject_id: int, item_id: int, citation_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    found = load_passage(session, lesson_version(session, item_id), citation_id)
    if found is None or found["sentence"].item_id != item_id:
        abort(404)
    return render_template(
        "today/_passage.html", passage=found, subject=subject, item_id=item_id
    )


@bp.post("/lessons/<int:item_id>/chat")
def ask(subject_id: int, item_id: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    back = url_for("today.lesson", subject_id=subject.id, item_id=item_id)
    text = request.form.get("question", "")
    scope = request.form.get("scope", "sources")
    web_enabled = g.teacher.web_search_enabled
    shown = scope if scope in assistant.SCOPES and web_enabled else "sources"
    if ollama_down():
        if is_htmx():
            return chat_partial(subject, item_id, scope=shown)
        return redirect(back, 303)
    try:
        assistant.ask(
            session,
            g.teacher.id,
            subject.id,
            item_id,
            text,
            scope=scope,
            web_enabled=web_enabled,
        )
    except ValueError as exc:
        if is_htmx():
            # 200, not 422: HTMX doesn't swap error responses.
            return chat_partial(
                subject, item_id, chat_error=str(exc), question=text, scope=shown
            )
        return redirect(back + "#assistant", 303)
    session.commit()
    if is_htmx():
        return chat_partial(subject, item_id, scope=shown)
    return redirect(back + "#assistant", 303)


def own_question(session, subject, item_id: int, question_id: int) -> ChatMessage:
    question = session.get(ChatMessage, question_id)
    if (
        question is None
        or question.role != "teacher"
        or question.subject_id != subject.id
        or question.item_id != item_id
    ):
        abort(404)
    return question


@bp.get("/lessons/<int:item_id>/chat/<int:question_id>")
def chat_answer(subject_id: int, item_id: int, question_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    question = own_question(session, subject, item_id, question_id)
    answer = assistant.pending_answer(session, question)
    if answer is not None:
        return render_template(
            "today/_message.html", message=answer, subject=subject, item_id=item_id
        )
    job = session.get(Job, question.job_id) if question.job_id else None
    return render_template(
        "today/_message.html",
        pending=question,
        job=job,
        subject=subject,
        item_id=item_id,
    )


@bp.get("/lessons/<int:item_id>/chat/<int:message_id>/chunks/<int:chunk_id>")
def chat_passage(subject_id: int, item_id: int, message_id: int, chunk_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    message = session.get(ChatMessage, message_id)
    if (
        message is None
        or message.role != "assistant"
        or message.subject_id != subject.id
        or message.item_id != item_id
    ):
        abort(404)
    cites = [c for part in message.parts for c in part["cites"] if c[0] == chunk_id]
    if not cites:
        abort(404)
    chunk = session.get(Chunk, chunk_id)
    return render_template(
        "today/_chat_passage.html",
        number=cites[0][1],
        chunk=chunk,
        source=session.get(Source, chunk.source_id) if chunk else None,
        subject=subject,
        item_id=item_id,
    )


def lesson_sentences(session, item_id: int) -> list[Sentence]:
    return [
        s for s in lesson_version(session, item_id).sentences if s.item_id == item_id
    ]


@bp.get("/lessons/<int:item_id>/trivia")
def trivia_card(subject_id: int, item_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    return render_template(
        "today/_trivia.html",
        subject=subject,
        item_id=item_id,
        ollama_down=ollama_down(),
        trivia_url=url_for(
            "today.lesson", subject_id=subject.id, item_id=item_id, trivia=1
        )
        + "#trivia",
        **trivia_context(session, item_id, lesson_sentences(session, item_id)),
    )


@bp.post("/lessons/<int:item_id>/trivia")
def trivia_write(subject_id: int, item_id: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    trivia.lock_lesson(session, subject.id)
    if ollama_down() or trivia.busy(trivia.trivia_job(session, item_id)):
        return trivia_back(subject, item_id)
    try:
        trivia.start_write(
            session,
            g.teacher.id,
            subject.id,
            item_id,
            request.form.get("count", type=int),
        )
    except ValueError:
        abort(400)
    session.commit()
    return trivia_back(subject, item_id)


@bp.post("/lessons/<int:item_id>/trivia/questions/<int:index>")
def trivia_edit(subject_id: int, item_id: int, index: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    current = trivia.latest_set(session, item_id)
    if current is None:
        abort(404)
    if request.form.get("set_id", type=int) != current.id:
        flash(TRIVIA_CHANGED, "error")
        return trivia_back(subject, item_id)
    try:
        trivia.edit_question(
            session,
            current,
            index,
            request.form.get("question", ""),
            request.form.get("answer", ""),
        )
    except IndexError:
        abort(404)
    except ValueError as exc:
        flash(str(exc), "error")
        return trivia_back(subject, item_id, edit=index)
    except trivia.StaleSet, IntegrityError:
        # A parallel edit saved the next version after the set_id check.
        session.rollback()
        flash(TRIVIA_CHANGED, "error")
        return trivia_back(subject, item_id)
    session.commit()
    return trivia_back(subject, item_id)


@bp.post("/lessons/<int:item_id>/trivia/questions/<int:index>/new")
def trivia_new(subject_id: int, item_id: int, index: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    find_lesson(placement(session, subject), item_id)
    trivia.lock_lesson(session, subject.id)
    current = trivia.latest_set(session, item_id)
    if (
        ollama_down()
        or trivia.busy(trivia.trivia_job(session, item_id))
        or current is None
        or not 0 <= index < len(current.questions)
    ):
        return trivia_back(subject, item_id)
    if request.form.get("set_id", type=int) != current.id:
        flash(TRIVIA_CHANGED, "error")
        return trivia_back(subject, item_id)
    trivia.start_replace(session, g.teacher.id, subject.id, item_id, current.id, index)
    session.commit()
    return trivia_back(subject, item_id)
