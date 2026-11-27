import os
import shutil
import tarfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from chalkboard import backup, create_app, jobs
from chalkboard.backup import (
    KEEP,
    NOT_A_BACKUP,
    NOT_EMPTY,
    BackupError,
    archive_name,
    backup_due,
    backup_time,
    create_backup,
    pg_env,
    prune,
    restore_backup,
)
from chalkboard.config import Settings
from chalkboard.db import SESSIONMAKER_KEY
from chalkboard.models import Chunk, Source, Subject, Teacher
from chalkboard.passwords import hash_password
from tests.conftest import (
    TEST_DATABASE_URL,
    csrf_token,
    recreate_database,
    run_migrations,
    sign_in,
)

MOMENT = datetime(2026, 11, 27, 9, 30, 5)


def stamped(folder: Path, moment: datetime) -> Path:
    path = folder / archive_name(moment)
    path.write_bytes(b"x")
    return path


def test_archive_name_and_time_round_trip():
    name = archive_name(MOMENT)
    assert name == "chalkboard-20261127-093005.tar.gz"
    assert backup_time(Path(name)) == MOMENT
    for junk in (
        "notes.txt",
        "chalkboard-oops.tar.gz",
        "chalkboard-20261127-093005.tar.gz.part",
    ):
        assert backup_time(Path(junk)) is None


def test_prune_keeps_newest_14(tmp_path):
    made = [stamped(tmp_path, MOMENT + timedelta(days=i)) for i in range(KEEP + 2)]
    (tmp_path / "notes.txt").write_text("keep me")
    prune(tmp_path)
    assert [p.exists() for p in made] == [False] * 2 + [True] * KEEP
    assert (tmp_path / "notes.txt").exists()


def test_backup_due(tmp_path):
    assert backup_due(tmp_path, MOMENT)
    assert backup_due(tmp_path / "missing", MOMENT)
    stamped(tmp_path, MOMENT)
    assert not backup_due(tmp_path, MOMENT + timedelta(hours=1))
    assert backup_due(tmp_path, MOMENT + timedelta(hours=25))


def test_pg_env_keeps_password_out_of_args():
    env = pg_env("postgresql+psycopg://me:s3cret@db.example:5433/chalk")
    assert (env["PGUSER"], env["PGPASSWORD"], env["PGDATABASE"]) == (
        "me",
        "s3cret",
        "chalk",
    )
    assert (env["PGHOST"], env["PGPORT"]) == ("db.example", "5433")


def test_pg_env_passes_libpq_query_options(monkeypatch):
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    url = "postgresql+psycopg://me@db/chalk"
    env = pg_env(url + "?sslmode=verify-full&sslrootcert=/ca.pem&unknown=1")
    assert (env["PGSSLMODE"], env["PGSSLROOTCERT"]) == ("verify-full", "/ca.pem")
    assert "unknown" not in env
    plain = pg_env(url)
    assert "PGSSLMODE" not in plain and "PGSSLROOTCERT" not in plain


def test_run_command_reports_missing_and_failing_tools():
    with pytest.raises(BackupError, match="no-such-tool is not installed"):
        backup.run_command(["no-such-tool"], {})
    with pytest.raises(BackupError, match="false failed"):
        backup.run_command([shutil.which("false") or "false"], dict(os.environ))


def test_create_backup_packs_dump_and_files(tmp_path, monkeypatch):
    def fake_dump(args, env):
        Path(args[args.index("-f") + 1]).write_bytes(b"dump")

    monkeypatch.setattr(backup, "run_command", fake_dump)
    data = tmp_path / "data"
    (data / "sources").mkdir(parents=True)
    (data / "sources" / "1.pdf").write_bytes(b"pdf")
    (data / "sources" / "2.pdf.part").write_bytes(b"half")
    settings = Settings(data_dir=data, backup_dir=tmp_path / "backups")
    path = create_backup(settings, MOMENT)
    assert path.name == archive_name(MOMENT)
    with tarfile.open(path) as tar:
        names = tar.getnames()
    assert "db.dump" in names
    assert "data/sources/1.pdf" in names
    assert not any(n.endswith(".part") for n in names)
    assert [p.name for p in settings.backup_dir.iterdir()] == [path.name]


def test_failed_dump_leaves_no_archive(tmp_path, monkeypatch):
    def broken(args, env):
        raise BackupError("pg_dump failed")

    monkeypatch.setattr(backup, "run_command", broken)
    settings = Settings(data_dir=tmp_path / "data", backup_dir=tmp_path / "b")
    with pytest.raises(BackupError):
        create_backup(settings, MOMENT)
    assert list(settings.backup_dir.iterdir()) == []


def test_restore_refuses_non_empty_database(db_session, tmp_path):
    archive = tmp_path / archive_name(MOMENT)
    archive.write_bytes(b"")
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(BackupError, match=NOT_EMPTY):
        restore_backup(settings, db_session, archive)
    assert not (tmp_path / "data").exists()


@pytest.fixture
def empty_session():
    url = TEST_DATABASE_URL.replace("_test", "_empty_test")
    recreate_database(url)
    engine = create_engine(url)
    with engine.connect() as connection:
        yield connection, url
    engine.dispose()


def test_restore_rejects_foreign_archive(empty_session, tmp_path):
    connection, url = empty_session
    note = tmp_path / "note.txt"
    note.write_text("hi")
    archive = tmp_path / "other.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(note, arcname="note.txt")
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not a tar")
    settings = Settings(database_url=url, data_dir=tmp_path / "data")
    with Session(connection) as session:
        for bad in (archive, junk):
            with pytest.raises(BackupError, match=NOT_A_BACKUP):
                restore_backup(settings, session, bad)


@pytest.mark.skipif(
    shutil.which("pg_dump") is None and not os.environ.get("CI"),
    reason="needs pg_dump and pg_restore on PATH",
)
def test_back_up_now_archive_restores_into_empty_database(tmp_path):
    src_url = TEST_DATABASE_URL.replace("_test", "_backup_src_test")
    dst_url = TEST_DATABASE_URL.replace("_test", "_restore_test")
    recreate_database(src_url)
    run_migrations(src_url)
    recreate_database(dst_url)  # no migrations: restore brings the schema
    src_data = tmp_path / "data"
    backups = tmp_path / "backups"
    app = create_app(
        Settings(
            database_url=src_url,
            data_dir=src_data,
            backup_dir=backups,
            secret_key="test",
            daily_backup=False,
        )
    )
    app.config["TESTING"] = True
    vector = [0.25] * 1024
    engine = create_engine(src_url)
    with engine.begin() as connection:
        teacher_id = connection.execute(
            text(
                "INSERT INTO teachers (email, name, password_hash) "
                "VALUES ('aamina@example.com', 'Aamina', :h) RETURNING id"
            ),
            {"h": hash_password("correct horse")},
        ).scalar_one()
        subject_id = connection.execute(
            text(
                "INSERT INTO subjects (teacher_id, name, class_minutes, colour) "
                "VALUES (:t, 'Physics', 60, '#0E7A64') RETURNING id"
            ),
            {"t": teacher_id},
        ).scalar_one()
        source_id = connection.execute(
            text(
                "INSERT INTO sources (subject_id, number, name, kind, file_path, passages) "
                "VALUES (:s, 1, 'notes.txt', 'txt', 'x', 1) RETURNING id"
            ),
            {"s": subject_id},
        ).scalar_one()
        connection.execute(
            text("UPDATE sources SET file_path = :p WHERE id = :i"),
            {"p": f"sources/{source_id}.txt", "i": source_id},
        )
        connection.execute(
            text(
                "INSERT INTO chunks (source_id, position, text, embedding) "
                "VALUES (:i, 0, 'hello', CAST(:v AS vector))"
            ),
            {"i": source_id, "v": str(vector)},
        )
    (src_data / "sources").mkdir(parents=True)
    (src_data / "sources" / f"{source_id}.txt").write_text("hello")
    engine.dispose()

    client = app.test_client()
    sign_in(client)
    response = client.post(
        "/settings/backups", data={"csrf_token": csrf_token(client, "/settings")}
    )
    assert response.status_code == 302
    assert jobs.run_once(app)
    archives = list(backups.glob("chalkboard-*.tar.gz"))
    assert len(archives) == 1

    restored_data = tmp_path / "restored"
    restored = create_app(
        Settings(
            database_url=dst_url,
            data_dir=restored_data,
            backup_dir=tmp_path / "other",
            secret_key="test",
            daily_backup=False,
        )
    )
    result = restored.test_cli_runner().invoke(args=["restore", str(archives[0])])
    assert result.exit_code == 0, result.output
    engine = create_engine(dst_url)
    with engine.connect() as connection:
        assert connection.scalar(select(Teacher.email)) == "aamina@example.com"
        assert connection.scalar(select(Subject.name)) == "Physics"
        assert connection.scalar(select(func.count()).select_from(Chunk)) == 1
        assert list(connection.scalar(select(Chunk.embedding))) == vector
        assert connection.scalar(select(Source.file_path)) == f"sources/{source_id}.txt"
        assert connection.scalar(text("SELECT version_num FROM alembic_version"))
    engine.dispose()
    assert (restored_data / "sources" / f"{source_id}.txt").read_text() == "hello"
    for application in (app, restored):
        application.extensions[SESSIONMAKER_KEY].kw["bind"].dispose()


def test_backup_folder_inside_data_dir_is_not_archived(tmp_path, monkeypatch):
    def fake_dump(args, env):
        Path(args[args.index("-f") + 1]).write_bytes(b"dump")

    monkeypatch.setattr(backup, "run_command", fake_dump)
    data = tmp_path / "data"
    (data / "sources").mkdir(parents=True)
    (data / "sources" / "1.pdf").write_bytes(b"pdf")
    settings = Settings(data_dir=data, backup_dir=data / "backups")
    create_backup(settings, MOMENT)
    second = create_backup(settings, MOMENT.replace(second=MOMENT.second + 1))
    with tarfile.open(second) as tar:
        names = tar.getnames()
    assert "data/sources/1.pdf" in names
    assert not any(n.startswith("data/backups") for n in names)
