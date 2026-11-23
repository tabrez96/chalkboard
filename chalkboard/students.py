import csv
import io

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from sqlalchemy import select
from wtforms import StringField
from wtforms.validators import DataRequired, Length

from chalkboard.db import get_session
from chalkboard.models import Student
from chalkboard.settings_page import strip
from chalkboard.subjects import own_subject

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ROWS = 2000

bp = Blueprint("students", __name__, url_prefix="/subjects/<int:subject_id>/students")


@bp.app_template_global()
def initials(name: str) -> str:
    parts = name.split()
    if not parts:
        return "?"
    letters = parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")
    return letters.upper()


class CsvError(ValueError): ...


def parse_csv(data: bytes) -> list[tuple[str, str]]:
    """Rows of (name, note) from an uploaded CSV: one name per line, optional note after a comma."""
    if len(data) > MAX_IMPORT_BYTES:
        raise CsvError("The file is larger than 1 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvError("Save the file as CSV (UTF-8) and try again") from None
    rows: list[tuple[str, str]] = []
    first = True
    try:
        for n, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if not row or not row[0].strip():
                continue
            if first:
                first = False
                if row[0].strip().casefold() == "name":
                    continue
            name = " ".join(row[0].split())
            note = " ".join(row[1].split()) if len(row) > 1 else ""
            if len(name) > 100:
                raise CsvError(f"Line {n}: the name is longer than 100 characters")
            if len(note) > 200:
                raise CsvError(f"Line {n}: the note is longer than 200 characters")
            rows.append((name, note))
    except csv.Error:
        raise CsvError("Couldn't read the file as CSV") from None
    if len(rows) > MAX_IMPORT_ROWS:
        raise CsvError("Import at most 2000 students at a time")
    if not rows:
        raise CsvError(
            "No names found. Put one name per line, with an optional note after a comma"
        )
    return rows


class StudentForm(FlaskForm):
    name = StringField(
        "Name",
        filters=[strip],
        validators=[DataRequired(message="Enter a name"), Length(max=100)],
    )
    note = StringField("Note", filters=[strip], validators=[Length(max=200)])


class ImportForm(FlaskForm):
    file = FileField("CSV file", validators=[FileRequired(message="Choose a CSV file")])


def render_page(subject, form=None, editing=None, import_form=None, status=200):
    return (
        render_template(
            "students/index.html",
            subject=subject,
            active="students.index",
            students=subject.students,
            form=form,
            editing=editing,
            import_form=import_form,
        ),
        status,
    )


def own_student(subject, student_id: int) -> Student:
    student = get_session().scalar(
        select(Student).where(
            Student.id == student_id, Student.subject_id == subject.id
        )
    )
    if student is None:
        abort(404)
    return student


def clean_name(name: str) -> str:
    return " ".join(name.split())


def is_duplicate(subject, name: str, editing: Student | None = None) -> bool:
    wanted = name.casefold()
    return any(
        s.name.casefold() == wanted for s in subject.students if s is not editing
    )


def back(subject):
    return redirect(url_for("students.index", subject_id=subject.id))


def save_form(subject, student: Student | None):
    form = StudentForm()
    if form.validate_on_submit():
        name = clean_name(form.name.data)
        if is_duplicate(subject, name, student):
            form.name.errors = [*form.name.errors, f"{name} is already on the list"]
        else:
            if student is None:
                student = Student(subject_id=subject.id)
                get_session().add(student)
            student.name = name
            student.note = form.note.data or ""
            get_session().commit()
            return back(subject)
    return render_page(subject, form=form, editing=student, status=400)


@bp.get("")
def index(subject_id: int):
    return render_page(own_subject(subject_id))


@bp.get("/new")
def new(subject_id: int):
    return render_page(own_subject(subject_id), form=StudentForm(formdata=None))


@bp.post("/new")
def create(subject_id: int):
    return save_form(own_subject(subject_id), None)


@bp.get("/<int:student_id>")
def edit(subject_id: int, student_id: int):
    subject = own_subject(subject_id)
    student = own_student(subject, student_id)
    form = StudentForm(formdata=None, data={"name": student.name, "note": student.note})
    return render_page(subject, form=form, editing=student)


@bp.post("/<int:student_id>")
def save(subject_id: int, student_id: int):
    subject = own_subject(subject_id)
    return save_form(subject, own_student(subject, student_id))


@bp.post("/<int:student_id>/delete")
def delete(subject_id: int, student_id: int):
    subject = own_subject(subject_id)
    student = own_student(subject, student_id)
    if not FlaskForm().validate_on_submit():
        abort(400)
    name = student.name
    get_session().delete(student)
    get_session().commit()
    flash(f"Deleted {name}")
    return back(subject)


@bp.get("/import")
def import_form(subject_id: int):
    return render_page(own_subject(subject_id), import_form=ImportForm(formdata=None))


@bp.post("/import")
def import_csv(subject_id: int):
    subject = own_subject(subject_id)
    form = ImportForm()
    if not form.validate_on_submit():
        return render_page(subject, import_form=form, status=400)
    try:
        rows = parse_csv(form.file.data.read(MAX_IMPORT_BYTES + 1))
    except CsvError as exc:
        form.file.errors = [str(exc)]
        return render_page(subject, import_form=form, status=400)
    seen = {s.name.casefold() for s in subject.students}
    added = skipped = 0
    for name, note in rows:
        if name.casefold() in seen:
            skipped += 1
            continue
        seen.add(name.casefold())
        subject.students.append(Student(name=name, note=note))
        added += 1
    get_session().commit()
    message = "Added 1 student" if added == 1 else f"Added {added} students"
    if skipped:
        message += f", skipped {skipped} already on the list"
    flash(message)
    return back(subject)
