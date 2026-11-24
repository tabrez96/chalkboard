"""The Chapters page: chapter list, next chapter card, draft review and request changes."""

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
from wtforms import DecimalField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from chalkboard import jobs
from chalkboard.auth import current_teacher
from chalkboard.chapters import (
    APPROVED,
    DRAFT,
    NOTES_MAX,
    TITLE_MAX,
    ChapterEntry,
    ChapterError,
    active_chapter_job,
    add_chapter,
    added_key,
    approved_overview,
    chapter_list,
    chapter_versions,
    enqueue_chapter,
    latest_chapter_job,
    next_chapter,
)
from chalkboard.db import get_session
from chalkboard.hours import class_count
from chalkboard.models import Approval, Job, PlanVersion, Sentence, Source, Subject
from chalkboard.overview import STEP_CHECK
from chalkboard.overview_page import (
    NOT_LATEST,
    OLLAMA_DOWN,
    amber_citation_ids,
    checked_post,
    format_hours,
    load_passage,
    ollama_down,
)
from chalkboard.subjects import own_subject

bp = Blueprint("chapters", __name__, url_prefix="/subjects/<int:subject_id>/chapters")

NO_OVERVIEW = "Approve the course overview first"
HAS_DRAFT = "This chapter already has a draft. Ask for changes on the draft instead."


def status_chip(entry: ChapterEntry, number: int | None) -> tuple[str, str]:
    """(label, css class) for the list chip; number is the latest version number."""
    if entry.status == APPROVED:
        return "Approved", "chip chip-ok"
    if entry.status == DRAFT:
        return f"Draft v{number}", "chip chip-draft"
    return "Not written", "chip"


def check_banner(count: int) -> tuple[str, str, str]:
    if count == 1:
        return (
            "1 sentence needs checking.",
            "Its cited passage doesn't say what the sentence says.",
            "Show it",
        )
    return (
        f"{count} sentences need checking.",
        "Their cited passages don't say what the sentence says.",
        "Show them",
    )


def hours_line(hours: Decimal | None, class_minutes: int) -> str:
    if hours is None:
        return ""
    line = f"{format_hours(hours)[:-2]} hour{'' if hours == 1 else 's'}"
    count = class_count(hours, class_minutes)
    if count is None:
        return line
    noun = "class" if count == 1 else "classes"
    return f"{line} · will become {count} {noun} of {class_minutes} min"


def progress_steps(job: Job, source_count: int) -> list[tuple[str, str, str]]:
    noun = "source" if source_count == 1 else "sources"
    labels = (
        f"Finding passages in your {source_count} {noun}",
        "Writing the sections",
        "Checking every sentence against its source",
    )
    step = job.step or ""
    detail = ""
    if step == STEP_CHECK:
        states = ("done", "done", "current")
    elif step.startswith("Writing section "):
        states = ("done", "current", "todo")
        detail = step.removeprefix("Writing ").capitalize()
    else:
        states = ("current", "todo", "todo")
    return [
        (label, state, detail if i == 1 else "")
        for i, (label, state) in enumerate(zip(labels, states, strict=True))
    ]


def fix_prefill(section: str, text: str) -> str:
    return f'Fix this sentence in "{section}": "{text}"'


class AddChapterForm(FlaskForm):
    title = StringField(
        "Title",
        validators=[
            DataRequired("Give the chapter a title"),
            Length(
                max=TITLE_MAX, message=f"Keep the title under {TITLE_MAX} characters"
            ),
        ],
    )
    hours = DecimalField(
        "Hours",
        places=1,
        validators=[
            Optional(),
            NumberRange(
                Decimal("0.1"), Decimal("999.9"), "Hours must be between 0.1 and 999.9"
            ),
        ],
    )
    notes = TextAreaField(
        "Notes",
        validators=[
            Length(
                max=NOTES_MAX, message=f"Keep the notes under {NOTES_MAX} characters"
            )
        ],
    )


def index_url(subject, **args) -> str:
    return url_for("chapters.index", subject_id=subject.id, **args)


def back(subject, key: str | None = None):
    return redirect(index_url(subject, chapter=key) if key else index_url(subject), 303)


def find_entry(session, subject, key: str) -> ChapterEntry:
    entry = next((e for e in chapter_list(session, subject) if e.key == key), None)
    if entry is None:
        abort(404)
    return entry


def source_count_of(session, subject) -> int:
    return session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.subject_id == subject.id, Source.is_syllabus.is_(False))
    )


def page_context(
    subject,
    key: str | None = None,
    version_number: int | None = None,
    cite_id: int | None = None,
    fix_id: int | None = None,
    write_errors: dict | None = None,
    write_values: dict | None = None,
    change_text: str = "",
    change_error: str = "",
    add_form: AddChapterForm | None = None,
) -> dict:
    session = get_session()
    entries = chapter_list(session, subject)
    has_overview = approved_overview(session, subject.id) is not None
    version_ids = [e.latest_version_id for e in entries if e.latest_version_id]
    numbers = dict(
        session.execute(
            select(PlanVersion.id, PlanVersion.number).where(
                PlanVersion.id.in_(version_ids)
            )
        ).all()
    )
    rows = [
        (i + 1, e, status_chip(e, numbers.get(e.latest_version_id)))
        for i, e in enumerate(entries)
    ]
    upcoming = next_chapter(entries)
    selected = upcoming
    if key:
        selected = next((e for e in entries if e.key == key), None)
        if selected is None:
            abort(404)
    number = entries.index(selected) + 1 if selected else None

    job = None
    versions: list[PlanVersion] = []
    if selected:
        job = latest_chapter_job(session, subject.id, selected.key)
        if selected.latest_version_id:
            versions = chapter_versions(session, subject.id, selected.key)
    latest = versions[0] if versions else None
    generating = job is not None and job.state in jobs.ACTIVE_STATES
    failed = (
        job
        if job
        and job.state == "failed"
        and (latest is None or job.created_at > latest.created_at)
        else None
    )

    if not entries:
        mode = "empty"
    elif generating:
        mode = "generating"
    elif key and latest:
        mode = "draft"
    else:
        mode = "card"

    version = None
    read_only = False
    approved_ids: set[int] = set()
    sections: list = []
    amber_ids: set[int] = set()
    banner = show_it = passage = None
    line = ""
    change = change_text
    if mode == "draft":
        version = latest
        if version_number is not None:
            version = next((v for v in versions if v.number == version_number), None)
            if version is None:
                abort(404)
        version = session.scalars(
            select(PlanVersion)
            .where(PlanVersion.id == version.id)
            .options(
                selectinload(PlanVersion.items),
                selectinload(PlanVersion.sentences).selectinload(Sentence.citations),
            )
        ).one()
        read_only = version.id != latest.id
        approved_ids = set(
            session.scalars(
                select(Approval.version_id).where(
                    Approval.version_id.in_([v.id for v in versions])
                )
            )
        )
        sections = [
            (
                item,
                [s for s in version.sentences if s.item_id == item.id and s.citations],
            )
            for item in version.items[1:]
        ]
        shown = [s for _, ss in sections for s in ss]
        amber_ids = amber_citation_ids(shown)
        flagged = [s for s in shown if s.check_flag]
        if flagged:
            banner = check_banner(len(flagged))
            first = flagged[0]
            first_amber = next(c.id for c in first.citations if c.id in amber_ids)
            show_it = (
                index_url(
                    subject,
                    chapter=selected.key,
                    version=version.number,
                    cite=first_amber,
                )
                + f"#sentence-{first.id}"
            )
        if cite_id:
            passage = load_passage(session, version, cite_id)
        if not change and fix_id and not read_only:
            for item, ss in sections:
                for s in ss:
                    if s.id == fix_id:
                        change = fix_prefill(item.title, s.text)
        line = hours_line(selected.hours, subject.class_minutes)

    values = write_values
    if values is None and selected:
        values = {"title": selected.title, "notes": selected.notes}
    steps = []
    if mode == "generating":
        steps = progress_steps(job, source_count_of(session, subject))
    return {
        "subject": subject,
        "active": "chapters.index",
        "mode": mode,
        "rows": rows,
        "has_overview": has_overview,
        "selected": selected,
        "key": selected.key if selected else "",
        "number": number,
        "upcoming": upcoming,
        "job": job,
        "steps": steps,
        "failed": failed,
        "versions": versions,
        "version": version,
        "latest": latest,
        "read_only": read_only,
        "approved_ids": approved_ids,
        "sections": sections,
        "amber_ids": amber_ids,
        "banner": banner,
        "show_it": show_it,
        "passage": passage,
        "hours_line": line,
        "next_number": (latest.number if latest else 0) + 1,
        "change_text": change,
        "change_error": change_error,
        "write_values": values or {},
        "write_errors": write_errors or {},
        "ollama_down": current_teacher().ollama_status == "unreachable",
        "add_form": add_form,
        "TITLE_MAX": TITLE_MAX,
        "NOTES_MAX": NOTES_MAX,
        "format_hours": format_hours,
        "index_url": lambda **a: index_url(subject, **a),
    }


def render_page(subject, status: int = 200, **kwargs):
    return (
        render_template("chapters/index.html", **page_context(subject, **kwargs)),
        status,
    )


@bp.get("")
def index(subject_id: int):
    subject = own_subject(subject_id)
    args = request.args
    return render_page(
        subject,
        key=args.get("chapter"),
        version_number=args.get("version", type=int),
        cite_id=args.get("cite", type=int),
        fix_id=args.get("fix", type=int),
    )


@bp.get("/progress")
def progress(subject_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    key = request.args.get("chapter", "")
    job = active_chapter_job(session, subject.id, key)
    if job is None:
        return "", 204
    return render_template(
        "chapters/_progress.html",
        job=job,
        steps=progress_steps(job, source_count_of(session, subject)),
        subject=subject,
        key=key,
    )


@bp.get("/citations/<int:citation_id>")
def passage(subject_id: int, citation_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    key = request.args.get("chapter", "")
    versions = chapter_versions(session, subject.id, key)
    number = request.args.get("version", type=int)
    version = next((v for v in versions if v.number == number), None)
    if version is None:
        abort(404)
    found = load_passage(session, version, citation_id)
    if found is None:
        abort(404)
    return render_template(
        "chapters/_passage.html",
        passage=found,
        subject=subject,
        version=version,
        key=key,
        read_only=version.id != versions[0].id,
    )


@bp.post("/write")
def write(subject_id: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    key = request.form.get("chapter", "")
    # Same subject lock as enqueue_chapter, taken before reading the chapter's latest version.
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    entry = find_entry(session, subject, key)
    if ollama_down():
        flash(OLLAMA_DOWN, "error")
        return back(subject, key)
    if entry.latest_version_id is not None:
        flash(HAS_DRAFT, "error")
        return back(subject, key)
    title = request.form.get("title", "")
    notes = request.form.get("notes", "")
    try:
        enqueue_chapter(session, subject, key, title, notes)
    except ChapterError as exc:
        field = "notes" if "notes" in str(exc) else "title"
        return render_page(
            subject,
            422,
            key=key,
            write_values={"title": title, "notes": notes},
            write_errors={field: str(exc)},
        )
    session.commit()
    return back(subject, key)


@bp.post("/request")
def request_changes(subject_id: int):
    subject = own_subject(subject_id)
    checked_post()
    session = get_session()
    key = request.form.get("chapter", "")
    find_entry(session, subject, key)
    if ollama_down():
        flash(OLLAMA_DOWN, "error")
        return back(subject, key)
    # Same subject lock as enqueue_chapter, taken before reading the latest version.
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    versions = chapter_versions(session, subject.id, key)
    if not versions or request.form.get("version", type=int) != versions[0].number:
        flash(NOT_LATEST, "error")
        return back(subject, key)
    latest = versions[0]
    head = latest.items[0]
    change = request.form.get("change", "")
    try:
        enqueue_chapter(
            session,
            subject,
            key,
            head.title,
            head.notes,
            parent=latest,
            change_request=change,
        )
    except ChapterError as exc:
        return render_page(
            subject, 422, key=key, change_text=change, change_error=str(exc)
        )
    session.commit()
    return back(subject, key)


def require_overview(session, subject) -> bool:
    if approved_overview(session, subject.id) is None:
        flash(NO_OVERVIEW, "error")
        return False
    return True


@bp.get("/new")
def new(subject_id: int):
    subject = own_subject(subject_id)
    if not require_overview(get_session(), subject):
        return back(subject)
    return render_page(subject, add_form=AddChapterForm(formdata=None))


@bp.post("/new")
def create(subject_id: int):
    subject = own_subject(subject_id)
    session = get_session()
    if not require_overview(session, subject):
        return back(subject)
    form = AddChapterForm()
    if not form.validate_on_submit():
        return render_page(subject, 400, add_form=form)
    try:
        row = add_chapter(
            session, subject, form.title.data, form.hours.data, form.notes.data or ""
        )
    except ChapterError as exc:
        form.title.errors.append(str(exc))
        return render_page(subject, 400, add_form=form)
    session.commit()
    return back(subject, added_key(row.id))
