"""S06 Today: the lesson placed on today's date, its steps and sources, and the empty states."""

from datetime import datetime

from flask import Blueprint, abort, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from chalkboard.chapters_page import time_ranges
from chalkboard.db import get_session
from chalkboard.lessons import placement, term_slots
from chalkboard.models import Chunk, Citation, PlanItem, PlanVersion, Sentence, Source
from chalkboard.overview_page import amber_citation_ids, load_passage
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
    return {
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
