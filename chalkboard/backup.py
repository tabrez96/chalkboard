"""Backups: archive the database and source files, prune, restore."""

import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, current_app
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from chalkboard import jobs
from chalkboard.config import Settings
from chalkboard.db import get_session
from chalkboard.jobs import JobContext

log = logging.getLogger(__name__)

KEEP = 14
PREFIX, SUFFIX = "chalkboard-", ".tar.gz"
STAMP = "%Y%m%d-%H%M%S"
DAILY = timedelta(hours=24)
LOCK_KEY = 230_001  # pg advisory lock: one backup at a time
# URL query options libpq understands, passed on to pg_dump / pg_restore
PG_QUERY_ENV = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslcrl": "PGSSLCRL",
    "connect_timeout": "PGCONNECT_TIMEOUT",
}
NOT_EMPTY = "The database is not empty. Restore needs an empty database."
NOT_A_BACKUP = "This file is not a Chalkboard backup."


class BackupError(Exception):
    """The message is shown as is (CLI or log)."""


def archive_name(moment: datetime) -> str:
    return PREFIX + moment.strftime(STAMP) + SUFFIX


def backup_time(path: Path) -> datetime | None:
    name = path.name
    if not (name.startswith(PREFIX) and name.endswith(SUFFIX)):
        return None
    try:
        return datetime.strptime(name[len(PREFIX) : -len(SUFFIX)], STAMP)
    except ValueError:
        return None


def list_backups(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found = [p for p in folder.glob(PREFIX + "*" + SUFFIX) if backup_time(p)]
    return sorted(found, key=lambda p: backup_time(p))


def latest_backup(folder: Path) -> Path | None:
    found = list_backups(folder)
    return found[-1] if found else None


def prune(folder: Path, keep: int = KEEP) -> None:
    found = list_backups(folder)
    for path in found[: max(len(found) - keep, 0)]:
        path.unlink(missing_ok=True)


def backup_due(folder: Path, now: datetime) -> bool:
    latest = latest_backup(folder)
    return latest is None or now - backup_time(latest) >= DAILY


def pg_env(database_url: str) -> dict[str, str]:
    url = make_url(database_url)
    env = dict(os.environ)
    parts = {
        "PGHOST": url.host,
        "PGPORT": None if url.port is None else str(url.port),
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }
    env.update({key: value for key, value in parts.items() if value is not None})
    for key, name in PG_QUERY_ENV.items():
        value = url.query.get(key)
        if isinstance(value, tuple):
            value = value[-1]
        if value is not None:
            env[name] = value
    return env


def run_command(args: list[str], env: dict[str, str]) -> None:
    try:
        result = subprocess.run(
            args, env=env, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise BackupError(f"{args[0]} is not installed") from None
    if result.returncode != 0:
        raise BackupError(result.stderr.strip() or f"{args[0]} failed")


def _data_filter(data_dir: Path, backup_dir: Path):
    """Tar filter: skip partial files and the backup folder when it sits inside the data folder."""
    skip = backup_dir.resolve()

    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if info.name.endswith(".part"):
            return None
        path = (data_dir / Path(info.name).relative_to("data")).resolve()
        return None if path == skip or skip in path.parents else info

    return keep


def create_backup(settings: Settings, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    folder = settings.backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / archive_name(now)
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        dump = Path(tmp) / "db.dump"
        part = Path(tmp) / (target.name + ".part")  # one file per writer
        run_command(
            ["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f", str(dump)],
            pg_env(settings.database_url),
        )
        with tarfile.open(part, "w:gz") as tar:
            tar.add(dump, arcname="db.dump")
            if settings.data_dir.exists():
                tar.add(
                    settings.data_dir,
                    arcname="data",
                    filter=_data_filter(settings.data_dir, folder),
                )
        os.replace(part, target)
    prune(folder)
    return target


def locked_backup(
    session: Session, settings: Settings, only_if_due: bool
) -> Path | None:
    if not session.scalar(select(func.pg_try_advisory_xact_lock(LOCK_KEY))):
        return None
    try:
        if only_if_due and not backup_due(settings.backup_dir, datetime.now()):
            return None
        return create_backup(settings)
    finally:
        session.rollback()  # releases the lock


def daily_backup(app: Flask) -> Path | None:
    settings = app.extensions["chalkboard.settings"]
    if not settings.daily_backup:
        return None
    with app.app_context():
        return locked_backup(get_session(), settings, only_if_due=True)


def restore_backup(settings: Settings, session: Session, archive: Path) -> None:
    tables = session.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
    )
    session.rollback()
    if tables:
        raise BackupError(NOT_EMPTY)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(root, filter="data")
        except tarfile.TarError:
            raise BackupError(NOT_A_BACKUP) from None
        dump = root / "db.dump"
        if not dump.is_file():
            raise BackupError(NOT_A_BACKUP)
        # Files first: if the copy fails the database is still empty and a retry works.
        if (root / "data").exists():
            shutil.copytree(root / "data", settings.data_dir, dirs_exist_ok=True)
        env = pg_env(settings.database_url)
        run_command(
            [
                "pg_restore",
                "--no-owner",
                "--no-privileges",
                "--single-transaction",  # a failed restore leaves the database empty
                "--exit-on-error",
                "-d",
                env["PGDATABASE"],
                str(dump),
            ],
            env,
        )


@jobs.register("backup", needs_ollama=False)
def backup_job(ctx: JobContext) -> None:
    settings = current_app.extensions["chalkboard.settings"]
    try:
        locked_backup(ctx.session, settings, only_if_due=False)
    except BackupError as exc:
        log.error("Backup failed: %s", exc)
        raise
