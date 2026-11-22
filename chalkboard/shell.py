from flask import Flask, current_app, url_for

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


def init_shell(app: Flask) -> None:
    app.jinja_env.globals.update(SUBJECT_PAGES=SUBJECT_PAGES, page_url=page_url)
