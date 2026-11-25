from dataclasses import dataclass
from datetime import date, datetime, time

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    url_for,
)
from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from sqlalchemy import select
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
    Regexp,
)

from chalkboard.auth import current_teacher, home_url, teacher_select
from chalkboard.db import get_session
from chalkboard.extract import kind_for
from chalkboard.models import (
    SUBJECT_COLOURS,
    AllowedSite,
    ClassTime,
    Source,
    Subject,
)
from chalkboard.placement import (
    Placed,
    coming_up,
    next_unfinished,
    short_when,
    wont_fit,
)
from chalkboard.settings_page import strip
from chalkboard.sources import MAX_UPLOAD_BYTES, UploadError
from chalkboard.syllabus import (
    PDF_ONLY,
    current_syllabus,
    unmark_syllabus,
    upload_syllabus,
    use_as_syllabus,
)
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
    syllabus_file = FileField("Syllabus PDF")
    # "" keeps what is there, "remove" unmarks, a number marks that source of this subject.
    syllabus = StringField(default="", validators=[Regexp(r"^(remove|\d+)?$")])

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
        upload = self.syllabus_file.data
        if (
            self.board.data != "none"
            and upload
            and getattr(upload, "filename", "")
            and kind_for(upload.filename) != "pdf"
        ):
            fail(self.syllabus_file, PDF_ONLY)
        return ok and not found


def next_colour(used: list[str]) -> str:
    for colour in SUBJECT_COLOURS:
        if colour not in used:
            return colour
    return SUBJECT_COLOURS[len(used) % len(SUBJECT_COLOURS)]


def board_name(board: str, state_board: str | None) -> str:
    return (state_board or "") if board == "state" else BOARD_LABELS.get(board, "")


def board_line(board: str, state_board: str | None, grade: str) -> str:
    label = board_name(board, state_board)
    return " · ".join(part for part in (label, grade) if part)


def schedule_line(times: dict[int, time], class_minutes: int) -> str:
    return f"{days_summary(times) or 'No timetable yet'} · {class_minutes} min"


def switcher_line(grade: str, class_minutes: int) -> str:
    return f"{grade} · {class_minutes} min" if grade else f"{class_minutes} min"


def now() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class CardPlan:
    next_when: str | None
    next_title: str | None
    wont_fit: int | None


@dataclass(frozen=True)
class ComingRow:
    when: datetime
    subject: str
    title: str
    minutes: int
    ready: bool
    url: str


def card_plan(placed: list[Placed], moment: datetime, timetable: bool) -> CardPlan:
    n = next_unfinished(placed, moment)
    return CardPlan(
        short_when(n.when) if n else None,
        n.lesson.title if n else None,
        # Without a timetable no class has a slot: that is a gap in setup, not an overflow.
        wont_fit(placed) if timetable else None,
    )


def coming_rows(subject: Subject, placed: list[Placed], day: date) -> list[ComingRow]:
    rows = []
    for p in coming_up(placed, day):
        ready = p.lesson.item_id is not None
        url = (
            url_for("today.lesson", subject_id=subject.id, item_id=p.lesson.item_id)
            if ready
            else url_for(
                "chapters.index", subject_id=subject.id, chapter=p.lesson.chapter_key
            )
        )
        rows.append(
            ComingRow(
                p.when, subject.name, p.lesson.title, p.lesson.minutes, ready, url
            )
        )
    return rows


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


def syllabus_display(choice: str, current_name: str, choices: dict[int, str]) -> str:
    """The file name the Syllabus PDF field shows; "" shows the empty field."""
    if choice == "remove":
        return ""
    if choice.isdigit():
        return choices.get(int(choice), "")
    return current_name


def save_syllabus(
    session, subject: Subject, form: SubjectForm, board_changed: bool
) -> None:
    if board_changed:
        unmark_syllabus(session, subject)
    if subject.board == "none":
        return
    upload = form.syllabus_file.data
    if upload and upload.filename:
        upload_syllabus(session, subject, upload.filename, upload.stream)
        return
    choice = form.syllabus.data or ""
    if choice == "remove":
        unmark_syllabus(session, subject)
    elif choice.isdigit():
        source = session.scalar(
            select(Source).where(
                Source.id == int(choice), Source.subject_id == subject.id
            )
        )
        if source is None:
            raise UploadError("Choose one of this subject's sources")
        use_as_syllabus(session, subject, source)


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
    subjects = (
        get_session().scalars(teacher_select(Subject).order_by(Subject.name)).all()
    )
    chip = None
    if form is not None:
        b = budget(
            form_times(form),
            form.term_start.data,
            form.term_end.data,
            form.class_minutes.data or 0,
        )
        chip = format_budget(b) if b is not None else None
    session = get_session()
    # Imported here: lessons imports modules that import this one.
    from chalkboard.lessons import placement, term_slots

    moment = now()
    today = moment.date()
    plans = {}
    coming = []
    for s in subjects:
        placed = placement(session, s)
        plans[s.id] = card_plan(placed, moment, bool(term_slots(s)))
        coming.extend(coming_rows(s, placed, today))
    coming.sort(key=lambda r: (r.when, r.subject))
    current = (
        current_syllabus(session, subject)
        if subject is not None and subject.board != "none"
        else None
    )
    choices = (
        [
            s
            for s in session.scalars(
                select(Source)
                .where(Source.subject_id == subject.id)
                .order_by(Source.number)
            )
            if s is not current
        ]
        if subject is not None
        else []
    )
    syllabus_name = (
        syllabus_display(
            form.syllabus.data or "",
            current.name if current else "",
            {s.id: s.name for s in choices},
        )
        if form is not None
        else ""
    )
    html = render_template(
        "subjects/index.html",
        syllabus_name=syllabus_name,
        syllabus_choices=choices,
        max_bytes=MAX_UPLOAD_BYTES,
        active="subjects",
        subjects=subjects,
        plans=plans,
        coming=coming,
        short_when=short_when,
        greeting=greeting(now().hour),
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
    session = get_session()
    apply(form, subject)
    session.flush()
    try:
        save_syllabus(session, subject, form, False)
    except UploadError as exc:
        # Nothing of this save is kept.
        session.rollback()
        form.syllabus_file.errors = [str(exc)]
        return render_index(form, status=400)
    session.commit()
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
    session = get_session()
    old_board = (subject.board, subject.state_board)
    apply(form, subject)
    session.flush()
    board_changed = old_board != (subject.board, subject.state_board)
    try:
        save_syllabus(session, subject, form, board_changed)
    except UploadError as exc:
        # Nothing of this save is kept.
        session.rollback()
        form.syllabus_file.errors = [str(exc)]
        return render_index(form, subject=own_subject(subject_id), status=400)
    session.commit()
    return redirect(url_for("subjects.index"))


@bp.get("/subjects/<int:subject_id>", endpoint="open")
def open_subject(subject_id: int):
    subject = own_subject(subject_id)
    current_teacher().last_subject_id = subject.id
    get_session().commit()
    return redirect(url_for("today.show", subject_id=subject.id))


@bp.get("/")
def root():
    return redirect(home_url(current_teacher()))
