import pytest
from flask import g
from sqlalchemy import Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from chalkboard.auth import current_teacher, teacher_select
from chalkboard.db import get_session
from chalkboard.models import Teacher


class OtherBase(DeclarativeBase):
    pass


class Note(OtherBase):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int]
    text: Mapped[str] = mapped_column(Text)


def test_teacher_select_filters_by_teacher(app, db_connection, db_session, teacher):
    other = Teacher(email="b@example.com", name="B", password_hash="x")
    db_session.add(other)
    db_session.commit()
    Note.__table__.create(db_connection)
    db_connection.execute(
        text(
            "INSERT INTO notes (teacher_id, text) VALUES (:a, 'mine'), (:b, 'theirs')"
        ),
        {"a": teacher.id, "b": other.id},
    )
    with app.test_request_context():
        g.teacher = teacher
        rows = get_session().scalars(teacher_select(Note)).all()
    assert [n.text for n in rows] == ["mine"]


def test_current_teacher_without_sign_in_raises(app):
    with app.test_request_context(), pytest.raises(RuntimeError):
        current_teacher()
