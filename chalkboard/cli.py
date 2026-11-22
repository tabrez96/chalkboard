import click
from flask.cli import with_appcontext
from sqlalchemy import select

from chalkboard.db import get_session
from chalkboard.models import Teacher
from chalkboard.passwords import MIN_LENGTH, hash_password, normalise_email


def _check_length(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise click.ClickException(
            f"Password must be at least {MIN_LENGTH} characters."
        )


def _find(email: str) -> Teacher | None:
    return get_session().scalar(select(Teacher).where(Teacher.email == email))


@click.command("create-teacher")
@click.option("--email", required=True)
@click.option("--name", required=True)
@click.password_option()
@with_appcontext
def create_teacher(email, name, password):
    """Create a teacher account."""
    _check_length(password)
    email = normalise_email(email)
    if _find(email) is not None:
        raise click.ClickException("A teacher with that email already exists.")
    db = get_session()
    db.add(Teacher(email=email, name=name, password_hash=hash_password(password)))
    db.commit()
    click.echo(f"Created teacher {email}.")


@click.command("reset-password")
@click.option("--email", required=True)
@click.password_option()
@with_appcontext
def reset_password(email, password):
    """Set a new password for a teacher account."""
    _check_length(password)
    email = normalise_email(email)
    teacher = _find(email)
    if teacher is None:
        raise click.ClickException("No teacher with that email.")
    teacher.password_hash = hash_password(password)
    get_session().commit()
    click.echo(f"Password reset for {email}.")
