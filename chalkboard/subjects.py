from flask import Blueprint, redirect, render_template

from chalkboard.auth import current_teacher, home_url

bp = Blueprint("subjects", __name__)


@bp.get("/subjects")
def index():
    return render_template("subjects/index.html")


@bp.get("/")
def root():
    return redirect(home_url(current_teacher()))
