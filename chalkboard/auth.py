from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf import FlaskForm
from sqlalchemy import Select, select
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired, InputRequired

from chalkboard.db import get_session
from chalkboard.models import Teacher
from chalkboard.passwords import (
    check_unknown_user,
    hash_password,
    needs_rehash,
    normalise_email,
    verify_password,
)

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"static"}


def public(view):
    """Mark a view as reachable without signing in."""
    view.is_public = True
    return view


def require_sign_in():
    """Default-deny guard: every view needs a signed-in teacher unless it is marked @public."""
    g.teacher = None
    teacher_id = session.get("teacher_id")
    if teacher_id:
        g.teacher = get_session().get(Teacher, teacher_id)
        if g.teacher is None:
            session.clear()
    if request.endpoint is None:
        return None
    view = current_app.view_functions[request.endpoint]
    if request.endpoint in PUBLIC_ENDPOINTS or getattr(view, "is_public", False):
        return None
    if g.teacher is None:
        target = None
        if request.method == "GET":
            target = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.login", next=target))
    return None


def current_teacher() -> Teacher:
    teacher = g.get("teacher")
    if teacher is None:
        raise RuntimeError("no signed-in teacher")
    return teacher


def teacher_select(model) -> Select:
    """A select over a teacher-owned model, limited to the signed-in teacher's rows."""
    return select(model).where(model.teacher_id == current_teacher().id)


def is_safe_next(target: str | None) -> bool:
    if not target or not target.startswith("/"):
        return False
    if target.startswith(("//", "/\\")):
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def home_url(teacher: Teacher) -> str:
    if (
        teacher.last_subject_id is not None
        and "today.show" in current_app.view_functions
    ):
        return url_for("today.show", subject_id=teacher.last_subject_id)
    return url_for("subjects.index")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired()])
    password = PasswordField("Password", validators=[InputRequired()])


@bp.route("/login", methods=["GET", "POST"])
@public
def login():
    if g.teacher is not None and request.method == "GET":
        return redirect(home_url(g.teacher))
    form = LoginForm()
    if form.validate_on_submit():
        db = get_session()
        teacher = db.scalar(
            select(Teacher).where(Teacher.email == normalise_email(form.email.data))
        )
        if teacher is None:
            check_unknown_user(form.password.data)
        elif verify_password(teacher.password_hash, form.password.data):
            if needs_rehash(teacher.password_hash):
                teacher.password_hash = hash_password(form.password.data)
                db.commit()
            session.clear()
            session["teacher_id"] = teacher.id
            session.permanent = True
            target = request.args.get("next")
            return redirect(target if is_safe_next(target) else home_url(teacher))
    if request.method == "POST":
        return render_template("auth/login.html", form=form, error=True), 401
    return render_template("auth/login.html", form=form, error=False)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
