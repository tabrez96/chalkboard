"""The Sources page: upload, save a web page, source table, extracted text and delete."""

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
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from werkzeug.exceptions import RequestEntityTooLarge

from chalkboard import jobs
from chalkboard.auth import current_teacher
from chalkboard.db import get_session
from chalkboard.extract import kind_for
from chalkboard.models import Chunk, Source
from chalkboard.sites import (
    SiteAccess,
    can_save_pages,
    may_fetch,
    on_allowed_list,
    page_origin,
    site_access,
)
from chalkboard.sources import (
    MAX_UPLOAD_BYTES,
    UNSUPPORTED,
    UploadError,
    affected_plans,
    clean_name,
    delete_source,
    status_label,
    status_tone,
    store_page,
    store_upload,
)
from chalkboard.subjects import board_line, own_subject
from chalkboard.syllabus import (
    NO_UNITS,
    current_syllabus,
    syllabus_status,
    syllabus_tone,
    type_units,
    unit_rows,
    use_as_syllabus,
)

bp = Blueprint("sources", __name__, url_prefix="/subjects/<int:subject_id>/sources")
bp.add_app_template_global(status_label)
bp.add_app_template_global(status_tone)

LINK_MESSAGE = "Paste a page link, like https://openstax.org/books/physics"


def sites_line(web_access: str, domains: list[str], open_mode: bool) -> tuple[str, str]:
    """The read-only line under the add bar and the endpoint its Change link opens."""
    if web_access == "uploads" or (not domains and not open_mode):
        return "Uploads only", "subjects.settings"
    if open_mode:
        return "Any website is allowed", "settings.show"
    return f"Pages from {', '.join(domains)}", "subjects.settings"


def summary_line(count: int, passages: int, syllabus: bool = False) -> str:
    line = (
        f"{count} source{'' if count == 1 else 's'} · "
        f"{passages} passage{'' if passages == 1 else 's'}"
    )
    return f"Syllabus + {line}" if syllabus else line


@bp.app_template_global()
def pages_label(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    if end is None or end == start:
        return f"Page {start}"
    return f"Pages {start}–{end}"


def subject_sources(subject) -> list[Source]:
    return list(
        get_session().scalars(
            select(Source)
            .where(Source.subject_id == subject.id)
            .order_by(Source.number)
            .options(selectinload(Source.job))
        )
    )


def own_source(subject, source_id: int) -> Source:
    source = get_session().scalar(
        select(Source).where(Source.id == source_id, Source.subject_id == subject.id)
    )
    if source is None:
        abort(404)
    return source


def own_syllabus(subject, source_id: int) -> Source:
    source = own_source(subject, source_id)
    if not source.is_syllabus or subject.board == "none":
        abort(404)
    return source


def back(subject):
    return redirect(url_for("sources.index", subject_id=subject.id), 303)


def page_context(
    subject,
    confirm: Source | None = None,
    page_error: dict | None = None,
    link_value: str = "",
    use_confirm: Source | None = None,
    units_for: Source | None = None,
    units_error: str = "",
) -> dict:
    session = get_session()
    follows_board = subject.board != "none"
    # With board "none" a leftover syllabus flag shows as a normal numbered row.
    syllabus = current_syllabus(session, subject) if follows_board else None
    rows = [s for s in subject_sources(subject) if s is not syllabus]
    unit_count = len(unit_rows(session, syllabus.id)) if syllabus else 0
    label = syllabus_status(syllabus, unit_count) if syllabus else ""
    line, change = sites_line(
        subject.web_access,
        [s.domain for s in subject.sites],
        current_teacher().allow_any_site,
    )
    active = rows + ([syllabus] if syllabus else [])
    return {
        "subject": subject,
        "active": "sources.index",
        "sources": rows,
        "summary": summary_line(
            len(rows), sum(s.passages for s in rows), syllabus is not None
        )
        if rows or syllabus
        else "",
        "follows_board": follows_board,
        "syllabus": syllabus,
        "syllabus_line": board_line(subject.board, subject.state_board, subject.grade),
        "syllabus_label": label,
        "syllabus_tone": syllabus_tone(syllabus, unit_count) if syllabus else "",
        "unit_count": unit_count,
        # See units / Type the units show once the reading is done.
        "units_available": bool(unit_count) or label == NO_UNITS,
        "use_confirm": use_confirm,
        "old_syllabus": syllabus if use_confirm else None,
        "units_for": units_for,
        "units": unit_rows(session, units_for.id) if units_for else [],
        "units_error": units_error,
        "sites_text": line,
        "change_url": (
            url_for(change, subject_id=subject.id)
            if change == "subjects.settings"
            else url_for(change)
        ),
        "polling": confirm is None
        and use_confirm is None
        and units_for is None
        and any(
            s.job is not None and s.job.state in jobs.ACTIVE_STATES for s in active
        ),
        "confirm": confirm,
        "affected": affected_plans(session, confirm) if confirm else [],
        "max_bytes": MAX_UPLOAD_BYTES,
        "can_save_pages": can_save_pages(site_access(subject, current_teacher())),
        "page_error": page_error,
        "link_value": link_value,
    }


@bp.get("")
def index(subject_id: int):
    return render_template(
        "sources/index.html", **page_context(own_subject(subject_id))
    )


@bp.get("/live")
def live(subject_id: int):
    return render_template(
        "sources/_live.html", oob=True, **page_context(own_subject(subject_id))
    )


@bp.post("/upload")
def upload(subject_id: int):
    subject = own_subject(subject_id)
    if not FlaskForm().validate_on_submit():
        abort(400)
    files = [f for f in request.files.getlist("file") if f.filename]
    if not files:
        flash("Choose a file to upload", "error")
        return back(subject)
    bad = [f for f in files if kind_for(f.filename) is None]
    if bad:  # nothing is saved when any file has the wrong type
        flash(f"{clean_name(bad[0].filename)}: {UNSUPPORTED}", "error")
        return back(subject)
    for f in files:
        try:
            store_upload(get_session(), subject, f.filename, f.stream)
        except UploadError as exc:
            flash(f"{clean_name(f.filename)}: {exc}", "error")
    return back(subject)


def link_error(subject, url: str) -> dict:
    """What to tell the teacher about a link `may_fetch` refused for this subject."""
    if not may_fetch(url, SiteAccess(open_mode=True, domains=frozenset())):
        return {"text": LINK_MESSAGE}
    text = f"This site isn't chosen for {subject.name}"
    if on_allowed_list(url, current_teacher()):
        return {
            "text": text,
            "link": url_for("subjects.settings", subject_id=subject.id),
            "link_text": "Change in subject settings",
        }
    return {
        "text": text,
        "link": url_for("settings.show"),
        "link_text": "Add it to the allowed sites in Settings",
    }


@bp.post("/save-page")
def save_page(subject_id: int):
    subject = own_subject(subject_id)
    if not FlaskForm().validate_on_submit():
        abort(400)
    access = site_access(subject, current_teacher())
    if not can_save_pages(access):
        abort(400)
    url = request.form.get("url", "").strip()
    if url and "://" not in url:
        url = "https://" + url
    if may_fetch(url, access):
        store_page(get_session(), subject, url)
        flash(f"Saving {page_origin(url)}")
        return back(subject)
    context = page_context(subject, page_error=link_error(subject, url), link_value=url)
    return render_template("sources/index.html", **context), 422


@bp.app_errorhandler(RequestEntityTooLarge)
def too_large(exc):
    if request.endpoint != "sources.upload":
        return exc
    flash("The file is larger than 50 MB", "error")
    return redirect(
        url_for("sources.index", subject_id=request.view_args["subject_id"]), 303
    )


@bp.get("/<int:source_id>")
def text(subject_id: int, source_id: int):
    subject = own_subject(subject_id)
    source = own_source(subject, source_id)
    chunks = get_session().scalars(
        select(Chunk).where(Chunk.source_id == source.id).order_by(Chunk.position)
    )
    return render_template(
        "sources/text.html",
        subject=subject,
        active="sources.index",
        source=source,
        chunks=list(chunks),
    )


@bp.get("/<int:source_id>/delete")
def confirm_delete(subject_id: int, source_id: int):
    subject = own_subject(subject_id)
    source = own_source(subject, source_id)
    return render_template(
        "sources/index.html", **page_context(subject, confirm=source)
    )


@bp.post("/<int:source_id>/delete")
def delete(subject_id: int, source_id: int):
    subject = own_subject(subject_id)
    source = own_source(subject, source_id)
    if not FlaskForm().validate_on_submit():
        abort(400)
    name = source.name
    delete_source(get_session(), source)
    flash(f"Deleted {name}")
    return back(subject)


@bp.get("/<int:source_id>/syllabus")
def confirm_syllabus(subject_id: int, source_id: int):
    subject = own_subject(subject_id)
    source = own_source(subject, source_id)
    if subject.board == "none":
        abort(400)
    current = current_syllabus(get_session(), subject)
    if current is None or current is source:
        return back(subject)
    return render_template(
        "sources/index.html", **page_context(subject, use_confirm=source)
    )


@bp.post("/<int:source_id>/syllabus")
def use_syllabus(subject_id: int, source_id: int):
    subject = own_subject(subject_id)
    source = own_source(subject, source_id)
    if not FlaskForm().validate_on_submit() or subject.board == "none":
        abort(400)
    use_as_syllabus(get_session(), subject, source)
    flash(f"{source.name} is now the syllabus")
    return back(subject)


@bp.get("/<int:source_id>/units")
def units(subject_id: int, source_id: int):
    subject = own_subject(subject_id)
    source = own_syllabus(subject, source_id)
    return render_template(
        "sources/index.html", **page_context(subject, units_for=source)
    )


@bp.post("/<int:source_id>/units")
def save_units(subject_id: int, source_id: int):
    subject = own_subject(subject_id)
    source = own_syllabus(subject, source_id)
    if not FlaskForm().validate_on_submit():
        abort(400)
    try:
        saved = type_units(get_session(), source, request.form.get("units", ""))
    except ValueError as exc:
        context = page_context(subject, units_for=source, units_error=str(exc))
        return render_template("sources/index.html", **context), 422
    flash(f"Saved {saved} unit{'' if saved == 1 else 's'}")
    return back(subject)
