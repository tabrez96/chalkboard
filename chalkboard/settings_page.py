from flask import Blueprint, abort, redirect, render_template, url_for
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import AnyOf

from chalkboard.auth import current_teacher, is_safe_next
from chalkboard.db import get_session

THEMES = ("light", "dark", "system")

bp = Blueprint("settings", __name__, url_prefix="/settings")


class ThemeForm(FlaskForm):
    theme = StringField(validators=[AnyOf(THEMES)])
    next = StringField()


class SidebarForm(FlaskForm):
    collapsed = StringField(validators=[AnyOf(["0", "1"])])
    next = StringField()


def back(target: str | None, default: str):
    return redirect(target if is_safe_next(target) else default)


@bp.get("")
def show():
    return render_template("settings/index.html", active="settings")


@bp.post("/theme")
def theme():
    form = ThemeForm()
    if not form.validate_on_submit():
        abort(400)
    current_teacher().theme = form.theme.data
    get_session().commit()
    return back(form.next.data, url_for("settings.show"))


@bp.post("/sidebar")
def sidebar():
    form = SidebarForm()
    if not form.validate_on_submit():
        abort(400)
    current_teacher().sidebar_collapsed = form.collapsed.data == "1"
    get_session().commit()
    return back(form.next.data, url_for("subjects.index"))
