from sqlalchemy import select

from chalkboard.models import Teacher
from chalkboard.passwords import verify_password
from tests.conftest import sign_in


def invoke(app, args, password):
    return app.test_cli_runner().invoke(args=args, input=f"{password}\n{password}\n")


def test_create_teacher(app, db_session):
    result = invoke(
        app,
        ["create-teacher", "--email", "New@Example.com", "--name", "Sam"],
        "longpassword",
    )
    assert result.exit_code == 0
    row = db_session.scalar(select(Teacher).where(Teacher.email == "new@example.com"))
    assert row.name == "Sam"
    assert row.password_hash.startswith("$argon2id$")
    assert verify_password(row.password_hash, "longpassword")


def test_create_teacher_duplicate(app, teacher):
    result = invoke(
        app,
        ["create-teacher", "--email", "aamina@example.com", "--name", "Sam"],
        "longpassword",
    )
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_short_password_rejected(app):
    result = invoke(
        app, ["create-teacher", "--email", "a@example.com", "--name", "A"], "short12"
    )
    assert result.exit_code == 1
    assert "at least 8" in result.output


def test_reset_password(app, client, db_session, teacher):
    result = invoke(
        app, ["reset-password", "--email", "aamina@example.com"], "brand new pass"
    )
    assert result.exit_code == 0
    db_session.refresh(teacher)
    assert not verify_password(teacher.password_hash, "correct horse")
    assert verify_password(teacher.password_hash, "brand new pass")
    assert sign_in(client, password="brand new pass").status_code == 302


def test_reset_password_unknown(app):
    result = invoke(
        app, ["reset-password", "--email", "nobody@example.com"], "longpassword"
    )
    assert result.exit_code == 1
    assert "No teacher with that email." in result.output
