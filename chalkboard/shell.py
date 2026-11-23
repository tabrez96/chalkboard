from flask import Flask, current_app, url_for

from chalkboard.ai import config_for
from chalkboard.auth import teacher_select
from chalkboard.db import get_session
from chalkboard.models import Subject
from chalkboard.net import netloc
from chalkboard.subjects import board_line, schedule_line, switcher_line, times_of

SUBJECT_PAGES = [
    ("today.show", "Today", "calendar"),
    ("overview.show", "Course overview", "overview"),
    ("chapters.index", "Chapters", "book"),
    ("sources.index", "Sources", "file"),
    ("students.index", "Students", "users"),
]


def page_url(endpoint: str, subject_id: int) -> str | None:
    """URL of a subject page, or None until the ticket that builds it registers the view."""
    if endpoint not in current_app.view_functions:
        return None
    return url_for(endpoint, subject_id=subject_id)


def teacher_subjects() -> list[Subject]:
    """The signed-in teacher's subjects by name; only runs when the sidebar asks."""
    return get_session().scalars(teacher_select(Subject).order_by(Subject.name)).all()


def init_shell(app: Flask) -> None:
    app.jinja_env.globals.update(
        SUBJECT_PAGES=SUBJECT_PAGES,
        page_url=page_url,
        netloc=netloc,
        teacher_subjects=teacher_subjects,
        board_line=board_line,
        schedule_line=schedule_line,
        switcher_line=switcher_line,
        times_of=times_of,
        ollama_host=lambda teacher: netloc(config_for(teacher).url),
    )
