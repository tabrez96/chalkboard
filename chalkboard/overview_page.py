"""The Course overview page: review, citations, request changes, approve."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_wtf import FlaskForm
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from chalkboard import jobs, overview, plans
from chalkboard.auth import current_teacher
from chalkboard.db import get_session
from chalkboard.hours import BudgetSummary, class_count, summarise
from chalkboard.models import (
    Approval,
    Chunk,
    Citation,
    Job,
    PlanItem,
    PlanVersion,
    Sentence,
    Source,
)
from chalkboard.overview import OverviewError
from chalkboard.subjects import board_line, own_subject, times_of
from chalkboard.syllabus import current_syllabus, unit_rows
from chalkboard.timetable import budget, days_summary, format_duration

bp = Blueprint("overview", __name__, url_prefix="/subjects/<int:subject_id>/overview")

OLLAMA_DOWN = "Can't reach the Ollama server. Check the connection, then try again."
NOT_LATEST = "Only the latest version can be changed or approved"
NO_SOURCES_YET = "Add sources first: the overview is written only from them"


def format_hours(hours: Decimal | None) -> str:
    if hours is None:
        return "–"
    text = f"{hours:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} h"


def classes_label(count: int | None) -> str:
    if count is None:
        return "Estimate"
    return f"{count} {'class' if count == 1 else 'classes'} · Estimate"


def flagged_sources(reason: str | None) -> set[int]:
    return {int(n) for n in re.findall(r"Source (\d+):", reason or "")}


def check_banner(count: int) -> tuple[str, str]:
    if count == 1:
        return "1 sentence needs checking.", "Its passage says something different."
    return (
        f"{count} sentences need checking.",
        "Their passages say something different.",
    )


def bar_parts(planned: int, budget_minutes: int) -> tuple[int, int]:
    """Percent widths (within the term, over the term) of the budget bar."""
    if planned == 0 and budget_minutes == 0:
        return 0, 0
    if planned <= budget_minutes:
        return round(100 * planned / budget_minutes), 0
    ok = round(100 * budget_minutes / planned)
    return ok, 100 - ok


def term_line(classes: int, times: Mapping[int, time], first: date, last: date) -> str:
    noun = "class" if classes == 1 else "classes"
    return (
        f"{classes} {noun} · {days_summary(times)} · "
        f"{first.day} {first:%b} – {last.day} {last:%b}"
    )


def progress_steps(job: Job, source_count: int) -> list[tuple[str, str, str]]:
    noun = "source" if source_count == 1 else "sources"
    labels = (
        f"Finding passages in your {source_count} {noun}",
        "Writing the overview",
        "Checking every sentence against its source",
    )
    step = job.step or ""
    detail = ""
    if step == overview.STEP_CHECK:
        states = ("done", "done", "current")
    elif step.startswith("Writing chapter "):
        states = ("done", "current", "todo")
        detail = step.removeprefix("Writing ").capitalize()
    else:
        states = ("current", "todo", "todo")
    return [
        (label, state, detail if i == 1 else "")
        for i, (label, state) in enumerate(zip(labels, states, strict=True))
    ]


def fix_prefill(chapter_number: int, text: str) -> str:
    return f'Fix this sentence in Chapter {chapter_number}: "{text}"'


@dataclass(frozen=True)
class ChapterRow:
    number: int
    item: PlanItem
    hours: str
    classes: str
    spill: str | None
    sentences: list[Sentence]


def chapter_rows(
    version: PlanVersion, class_minutes: int, summary: BudgetSummary | None
) -> list[ChapterRow]:
    spills = {s.chapter: s.label for s in summary.spills} if summary else {}
    by_item: dict[int, list[Sentence]] = {}
    for sentence in version.sentences:
        if sentence.item_id is not None and sentence.citations:
            by_item.setdefault(sentence.item_id, []).append(sentence)
    return [
        ChapterRow(
            i + 1,
            item,
            format_hours(item.hours_estimate),
            classes_label(class_count(item.hours_estimate, class_minutes)),
            spills.get(i),
            by_item.get(item.id, []),
        )
        for i, item in enumerate(version.items)
    ]


def amber_citation_ids(sentences: list[Sentence]) -> set[int]:
    """Citations of the flagged sentences that the checker named (all of them when it named none)."""
    ids = set()
    for s in sentences:
        if not s.check_flag:
            continue
        named = flagged_sources(s.check_reason)
        ids.update(c.id for c in s.citations if not named or c.source_number in named)
    return ids


def load_passage(session, version: PlanVersion, citation_id: int) -> dict | None:
    row = session.execute(
        select(Citation, Sentence)
        .join(Sentence, Sentence.id == Citation.sentence_id)
        .where(Citation.id == citation_id, Sentence.version_id == version.id)
    ).first()
    if row is None:
        return None
    citation, sentence = row
    chunk = source = None
    if citation.chunk_id is not None:
        chunk, source = session.execute(
            select(Chunk, Source)
            .join(Source, Source.id == Chunk.source_id)
            .where(Chunk.id == citation.chunk_id)
        ).one()
    return {
        "citation": citation,
        "sentence": sentence,
        "chunk": chunk,
        "source": source,
        "flagged": citation.id in amber_citation_ids([sentence]),
    }


def show_url(subject, **args) -> str:
    return url_for("overview.show", subject_id=subject.id, **args)


def page_context(
    subject,
    version_number: int | None = None,
    cite_id: int | None = None,
    fix_id: int | None = None,
    change_text: str = "",
    change_error: str = "",
) -> dict:
    session = get_session()
    versions = overview.overview_versions(session, subject.id)
    latest = versions[0] if versions else None
    version = latest
    if version_number is not None:
        version = next((v for v in versions if v.number == version_number), None)
        if version is None:
            abort(404)
    if version is not None:
        version = session.scalars(
            select(PlanVersion)
            .where(PlanVersion.id == version.id)
            .options(
                selectinload(PlanVersion.items),
                selectinload(PlanVersion.sentences).selectinload(Sentence.citations),
            )
        ).one()
    read_only = version is not None and version.id != latest.id
    approved_ids = set(
        session.scalars(
            select(Approval.version_id).where(
                Approval.version_id.in_([v.id for v in versions])
            )
        )
    )
    job = overview.latest_overview_job(session, subject.id)
    generating = job is not None and job.state in jobs.ACTIVE_STATES
    failed = (
        job
        if job
        and job.state == "failed"
        and (latest is None or job.created_at > latest.created_at)
        else None
    )
    source_count = session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.subject_id == subject.id, Source.is_syllabus.is_(False))
    )
    follows_board = subject.board != "none"
    syllabus = current_syllabus(session, subject) if follows_board else None
    unit_count = len(unit_rows(session, syllabus.id)) if syllabus else 0
    syllabus_chip = (
        f"{board_line(subject.board, subject.state_board, subject.grade)} · "
        f"{unit_count} unit{'' if unit_count == 1 else 's'}"
    )

    summary = None
    term = estimated = ""
    bar = (0, 0)
    rows: list[ChapterRow] = []
    amber_ids: set[int] = set()
    flagged: list[Sentence] = []
    banner = show_it = passage = None
    change = change_text
    if version is not None:
        m = subject.class_minutes
        times = times_of(subject)
        hours = [i.hours_estimate for i in version.items]
        summary = summarise(hours, m, times, subject.term_start, subject.term_end)
        if summary:
            b = budget(times, subject.term_start, subject.term_end, m)
            term = term_line(b.classes, times, subject.term_start, subject.term_end)
            bar = bar_parts(summary.planned_minutes, summary.budget_minutes)
        estimated = format_duration(sum(class_count(h, m) or 0 for h in hours) * m)
        rows = chapter_rows(version, m, summary)
        shown = [s for row in rows for s in row.sentences]
        amber_ids = amber_citation_ids(shown)
        flagged = [s for s in shown if s.check_flag]
        if flagged:
            banner = check_banner(len(flagged))
            first = flagged[0]
            first_amber = next(c.id for c in first.citations if c.id in amber_ids)
            show_it = (
                show_url(subject, version=version.number, cite=first_amber)
                + f"#sentence-{first.id}"
            )
        if cite_id:
            passage = load_passage(session, version, cite_id)
        if not change and fix_id and not read_only:
            for row in rows:
                for s in row.sentences:
                    if s.id == fix_id:
                        change = fix_prefill(row.number, s.text)
    return {
        "subject": subject,
        "active": "overview.show",
        "version": version,
        "latest": latest,
        "versions": versions,
        "read_only": read_only,
        "approved_ids": approved_ids,
        "generating": generating,
        "job": job,
        "steps": progress_steps(job, source_count) if generating else [],
        "failed": failed,
        "source_count": source_count,
        "summary": summary,
        "term": term,
        "bar": bar,
        "estimated": estimated,
        "rows": rows,
        "amber_ids": amber_ids,
        "banner": banner,
        "show_it": show_it,
        "passage": passage,
        "change_text": change,
        "change_error": change_error,
        "ollama_down": current_teacher().ollama_status == "unreachable",
        "next_number": (latest.number if latest else 0) + 1,
        "follows_board": follows_board,
        "syllabus": syllabus,
        "syllabus_chip": syllabus_chip,
    }


def back(subject):
    return redirect(url_for("overview.show", subject_id=subject.id), 303)


def checked_post() -> None:
    if not FlaskForm().validate_on_submit():
        abort(400)


def ollama_down() -> bool:
    return current_teacher().ollama_status == "unreachable"


@bp.get("")
def show(subject_id: int):
    subject = own_subject(subject_id)
    args = request.args
    return render_template(
        "overview/index.html",
        **page_context(
            subject,
            args.get("version", type=int),
            args.get("cite", type=int),
            args.get("fix", type=int),
        ),
    )


@bp.get("/progress")
def progress(subject_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    job = overview.active_overview_job(session, subject.id)
    if job is None:
        return "", 204
    source_count = session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.subject_id == subject.id, Source.is_syllabus.is_(False))
    )
    return render_template(
        "overview/_progress.html",
        job=job,
        steps=progress_steps(job, source_count),
        subject=subject,
    )


@bp.get("/citations/<int:citation_id>")
def passage(subject_id: int, citation_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    versions = overview.overview_versions(session, subject.id)
    number = request.args.get("version", type=int)
    version = next((v for v in versions if v.number == number), None)
    if version is None:
        abort(404)
    found = load_passage(session, version, citation_id)
    if found is None:
        abort(404)
    return render_template(
        "overview/_passage.html",
        passage=found,
        subject=subject,
        version=version,
        read_only=version.id != versions[0].id,
    )


@bp.post("/generate")
def generate(subject_id: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    if ollama_down():
        flash(OLLAMA_DOWN, "error")
        return back(subject)
    if overview.overview_versions(session, subject.id):
        abort(400)
    count = session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.subject_id == subject.id, Source.is_syllabus.is_(False))
    )
    if count == 0:
        flash(NO_SOURCES_YET, "error")
        return back(subject)
    overview.enqueue_overview(session, subject)
    session.commit()
    return back(subject)


@bp.post("/request")
def request_changes(subject_id: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    if ollama_down():
        flash(OLLAMA_DOWN, "error")
        return back(subject)
    latest = overview.latest_overview(session, subject.id)
    if latest is None or request.form.get("version", type=int) != latest.number:
        flash(NOT_LATEST, "error")
        return back(subject)
    change = request.form.get("change", "")
    try:
        overview.enqueue_overview(
            session, subject, parent=latest, change_request=change
        )
    except OverviewError as exc:
        return (
            render_template(
                "overview/index.html",
                **page_context(subject, change_text=change, change_error=str(exc)),
            ),
            422,
        )
    session.commit()
    return back(subject)


@bp.post("/approve")
def approve(subject_id: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    latest = overview.latest_overview(session, subject.id)
    if latest is None or request.form.get("version", type=int) != latest.number:
        flash(NOT_LATEST, "error")
        return back(subject)
    plans.approve(session, latest)
    session.commit()
    flash(f"Approved version {latest.number}")
    return back(subject)
