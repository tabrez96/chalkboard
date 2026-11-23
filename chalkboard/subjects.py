from datetime import datetime, time

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    url_for,
)
from flask_wtf import FlaskForm
from wtforms import (
    IntegerField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
)
from wtforms.fields import DateField, TimeField
from wtforms.validators import (
    AnyOf,
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Optional,
)

from chalkboard.auth import current_teacher, home_url, teacher_select
from chalkboard.db import get_session
from chalkboard.models import SUBJECT_COLOURS, AllowedSite, ClassTime, Subject
from chalkboard.settings_page import strip
from chalkboard.timetable import WEEKDAYS, budget, days_summary, format_budget

BOARD_CHOICES = [
    ("cbse", "CBSE"),
    ("icse", "ICSE"),
    ("state", "State board"),
    ("ib", "IB"),
    ("none", "Other or none"),
]
BOARD_LABELS = {"cbse": "CBSE", "icse": "ICSE", "ib": "IB"}
COLOUR_NAMES = {
    "#0E7A64": "Green",
    "#3A57C9": "Blue",
    "#B4492B": "Rust",
    "#7A4FB5": "Violet",
    "#A8326E": "Plum",
    "#2F6F8F": "Ocean",
}
SITES_MESSAGE = "Tick at least one site, or choose Uploads only"
STATE_MESSAGE = "Enter the state board's name, e.g. Maharashtra State Board"
TERM_BOTH_MESSAGE = "Enter both the first and last day of the term"
TERM_ORDER_MESSAGE = "The last day must be on or after the first day"
MINUTES_MESSAGE = "Enter a class length between 10 and 240 minutes"

bp = Blueprint("subjects", __name__)


class SubjectForm(FlaskForm):
    name = StringField(
        "Subject name",
        filters=[strip],
        validators=[DataRequired(message="Enter a name"), Length(max=80)],
    )
    grade = StringField("Grade", filters=[strip], validators=[Length(max=40)])
    class_minutes = IntegerField(
        "Class length",
        validators=[
            InputRequired(message=MINUTES_MESSAGE),
            NumberRange(10, 240, message=MINUTES_MESSAGE),
        ],
    )
    board = SelectField("Curriculum board", choices=BOARD_CHOICES, default="none")
    state_board = StringField(
        "State board name", filters=[strip], validators=[Length(max=100)]
    )
    days = SelectMultipleField(choices=list(enumerate(WEEKDAYS)), coerce=int)
    time_0 = TimeField(format="%H:%M", validators=[Optional()])
    time_1 = TimeField(format="%H:%M", validators=[Optional()])
    time_2 = TimeField(format="%H:%M", validators=[Optional()])
    time_3 = TimeField(format="%H:%M", validators=[Optional()])
    time_4 = TimeField(format="%H:%M", validators=[Optional()])
    time_5 = TimeField(format="%H:%M", validators=[Optional()])
    term_start = DateField("First day", validators=[Optional()])
    term_end = DateField("Last day", validators=[Optional()])
    web_access = StringField(default="uploads", validators=[AnyOf(["uploads", "web"])])
    # Choices are the teacher's allowed sites, set by the view; an id from another account is an invalid choice.
    sites = SelectMultipleField(coerce=int)
    colour = RadioField(choices=[(c, COLOUR_NAMES[c]) for c in SUBJECT_COLOURS])

    open_mode: bool = False

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        found = []

        def fail(field, message):
            field.errors = [*field.errors, message]
            found.append(field)

        if self.class_minutes.errors:
            self.class_minutes.errors = [MINUTES_MESSAGE]
        if self.board.data == "state" and not self.state_board.data:
            fail(self.state_board, STATE_MESSAGE)
        for day in valid_days(self):
            field = self[f"time_{day}"]
            if field.data is None:
                fail(field, f"Enter a start time for {WEEKDAYS[day]}")
        start, end = self.term_start.data, self.term_end.data
        if start is None and end is not None:
            fail(self.term_start, TERM_BOTH_MESSAGE)
        elif end is None and start is not None:
            fail(self.term_end, TERM_BOTH_MESSAGE)
        elif start and end and end < start:
            fail(self.term_end, TERM_ORDER_MESSAGE)
        if self.web_access.data == "web" and not self.open_mode and not self.sites.data:
            fail(self.sites, SITES_MESSAGE)
        return ok and not found


def next_colour(used: list[str]) -> str:
    for colour in SUBJECT_COLOURS:
        if colour not in used:
            return colour
    return SUBJECT_COLOURS[len(used) % len(SUBJECT_COLOURS)]


def board_line(board: str, state_board: str | None, grade: str) -> str:
    label = state_board if board == "state" else BOARD_LABELS.get(board)
    return " · ".join(part for part in (label, grade) if part)


def schedule_line(times: dict[int, time], class_minutes: int) -> str:
    return f"{days_summary(times) or 'No timetable yet'} · {class_minutes} min"


def switcher_line(grade: str, class_minutes: int) -> str:
    return f"{grade} · {class_minutes} min" if grade else f"{class_minutes} min"


def greeting(hour: int) -> str:
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 17 else "Good evening"


def times_of(subject) -> dict[int, time]:
    return {ct.weekday: ct.start for ct in subject.class_times}


def valid_days(form: SubjectForm) -> list[int]:
    # An invalid posted day keeps its choice error but has no time field to look up.
    return [day for day in form.days.data or [] if 0 <= day < len(WEEKDAYS)]


def form_times(form: SubjectForm) -> dict[int, time]:
    times = {}
    for day in valid_days(form):
        start = form[f"time_{day}"].data
        if start is not None:
            times[day] = start
    return times


def own_subject(subject_id: int) -> Subject:
    subject = get_session().scalar(
        teacher_select(Subject).where(Subject.id == subject_id)
    )
    if subject is None:
        abort(404)
    return subject


def build_form(subject: Subject | None, posted: bool = False) -> SubjectForm:
    teacher = current_teacher()
    session = get_session()
    if posted:
        form = SubjectForm()
    elif subject is not None:
        times = times_of(subject)
        data = {
            "name": subject.name,
            "grade": subject.grade,
            "class_minutes": subject.class_minutes,
            "board": subject.board,
            "state_board": subject.state_board or "",
            "days": list(times),
            "term_start": subject.term_start,
            "term_end": subject.term_end,
            "web_access": subject.web_access,
            "sites": [s.id for s in subject.sites],
            "colour": subject.colour,
        }
        data.update({f"time_{day}": start for day, start in times.items()})
        form = SubjectForm(formdata=None, data=data)
    else:
        used = list(session.scalars(teacher_select(Subject)).all())
        form = SubjectForm(
            formdata=None,
            data={
                "board": "none",
                "days": [],
                "sites": [],
                "web_access": "uploads",
                "colour": next_colour([s.colour for s in used]),
            },
        )
    allowed = session.scalars(teacher_select(AllowedSite).order_by(AllowedSite.domain))
    form.sites.choices = [(s.id, s.domain) for s in allowed]
    form.open_mode = teacher.allow_any_site
    return form


def render_index(form: SubjectForm | None = None, subject=None, status: int = 200):
    subjects = get_session().scalars(teacher_select(Subject).order_by(Subject.name))
    chip = None
    if form is not None:
        b = budget(
            form_times(form),
            form.term_start.data,
            form.term_end.data,
            form.class_minutes.data or 0,
        )
        chip = format_budget(b) if b is not None else None
    html = render_template(
        "subjects/index.html",
        active="subjects",
        subjects=subjects.all(),
        greeting=greeting(datetime.now().hour),
        form=form,
        editing=subject,
        chip=chip,
    )
    return html, status


def apply(form: SubjectForm, subject: Subject) -> None:
    session = get_session()
    subject.name = form.name.data
    subject.grade = form.grade.data or ""
    subject.class_minutes = form.class_minutes.data
    subject.board = form.board.data
    subject.state_board = form.state_board.data if form.board.data == "state" else None
    subject.term_start = form.term_start.data
    subject.term_end = form.term_end.data
    subject.colour = form.colour.data
    subject.web_access = form.web_access.data
    session.add(subject)
    # Flush the deletes first so the unique (subject_id, weekday) never clashes.
    subject.class_times.clear()
    session.flush()
    for day, start in sorted(form_times(form).items()):
        subject.class_times.append(ClassTime(weekday=day, start=start))
    if form.web_access.data == "uploads":
        subject.sites = []
    elif not form.open_mode:
        ids = form.sites.data
        subject.sites = list(
            session.scalars(
                teacher_select(AllowedSite)
                .where(AllowedSite.id.in_(ids))
                .order_by(AllowedSite.domain)
            )
        )
    # In open mode the checklist isn't shown, so ticked sites stay as they are.


@bp.get("/subjects")
def index():
    return render_index()


@bp.get("/subjects/new")
def new():
    return render_index(form=build_form(None))


@bp.post("/subjects/new")
def create():
    form = build_form(None, posted=True)
    if not form.validate_on_submit():
        return render_index(form, status=400)
    subject = Subject(teacher_id=current_teacher().id)
    apply(form, subject)
    get_session().commit()
    return redirect(url_for("subjects.index"))


@bp.get("/subjects/<int:subject_id>/settings")
def settings(subject_id: int):
    subject = own_subject(subject_id)
    return render_index(form=build_form(subject), subject=subject)


@bp.post("/subjects/<int:subject_id>/settings")
def save_settings(subject_id: int):
    subject = own_subject(subject_id)
    form = build_form(subject, posted=True)
    if not form.validate_on_submit():
        return render_index(form, subject=subject, status=400)
    apply(form, subject)
    get_session().commit()
    return redirect(url_for("subjects.index"))


@bp.get("/subjects/<int:subject_id>", endpoint="open")
def open_subject(subject_id: int):
    subject = own_subject(subject_id)
    current_teacher().last_subject_id = subject.id
    get_session().commit()
    if "today.show" in current_app.view_functions:
        return redirect(url_for("today.show", subject_id=subject.id))
    return render_template("subjects/subject.html", subject=subject, active=None)


@bp.get("/")
def root():
    return redirect(home_url(current_teacher()))
