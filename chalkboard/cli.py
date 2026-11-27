import logging
import signal
import threading
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import select

from chalkboard.backup import BackupError, create_backup, restore_backup
from chalkboard.db import get_session
from chalkboard.models import Teacher
from chalkboard.passwords import MIN_LENGTH, hash_password, normalise_email
from chalkboard.worker import run_worker


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


@click.command("worker")
@click.option(
    "--poll",
    default=2.0,
    show_default=True,
    help="Seconds between checks when the queue is empty.",
)
@with_appcontext
def worker(poll):
    """Run queued jobs until stopped."""
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    click.echo("Worker started.")
    run_worker(current_app._get_current_object(), poll, stop)


@click.command("backup")
@with_appcontext
def backup():
    """Write a backup archive now (database and source files)."""
    settings = current_app.extensions["chalkboard.settings"]
    try:
        path = create_backup(settings)
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Backup written to {path}.")


@click.command("restore")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@with_appcontext
def restore(archive):
    """Restore a backup archive into an empty database."""
    settings = current_app.extensions["chalkboard.settings"]
    try:
        restore_backup(settings, get_session(), archive)
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Restored {archive.name}.")
