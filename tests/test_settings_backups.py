from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text

from chalkboard import backup, jobs
from chalkboard.backup import LOCK_KEY, archive_name
from chalkboard.models import Job, Teacher
from chalkboard.passwords import hash_password
from chalkboard.settings_page import last_backup_line
from tests.conftest import TEST_DATABASE_URL, csrf_token, sign_in

NOON = datetime(2026, 11, 27, 12, 0)


@pytest.fixture
def folder(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.extensions["chalkboard.settings"], "backup_dir", tmp_path)
    return tmp_path


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def page(client):
    return client.get("/settings").get_data(as_text=True)


def post_backup(client):
    return client.post(
        "/settings/backups", data={"csrf_token": csrf_token(client, "/settings")}
    )


def backup_jobs(db_session):
    return db_session.scalars(select(Job).where(Job.kind == "backup")).all()


def test_no_backups_yet(signed_in, folder):
    html = page(signed_in)
    assert "No backups yet" in html
    assert "Back up now" in html
    assert "Download latest" not in html


def test_last_backup_line():
    assert last_backup_line(None, NOON) == "No backups yet"
    assert (
        last_backup_line(datetime(2026, 11, 27, 3, 5), NOON)
        == "Last backup: today, 03:05 · daily"
    )
    assert (
        last_backup_line(NOON - timedelta(days=1), NOON)
        == "Last backup: yesterday, 12:00 · daily"
    )
    assert (
        last_backup_line(datetime(2026, 11, 3, 4, 0), NOON)
        == "Last backup: 3 Nov, 04:00 · daily"
    )


def test_back_up_now_queues_one_job(signed_in, folder, db_session):
    assert post_backup(signed_in).status_code == 302
    assert post_backup(signed_in).status_code == 302
    assert len(backup_jobs(db_session)) == 1
    assert 'class="job-status job-queued"' in page(signed_in)


def test_back_up_now_needs_csrf(signed_in, folder, db_session):
    assert signed_in.post("/settings/backups").status_code == 400
    assert backup_jobs(db_session) == []


def test_download_latest(signed_in, folder):
    assert signed_in.get("/settings/backups/latest").status_code == 404
    (folder / archive_name(datetime(2026, 11, 1, 1, 0))).write_bytes(b"old")
    (folder / archive_name(datetime(2026, 11, 2, 1, 0))).write_bytes(b"new")
    assert "Download latest" in page(signed_in)
    response = signed_in.get("/settings/backups/latest")
    assert response.data == b"new"
    assert "attachment" in response.headers["Content-Disposition"]
    assert (
        archive_name(datetime(2026, 11, 2, 1, 0))
        in (response.headers["Content-Disposition"])
    )


def test_backups_hidden_with_two_accounts(signed_in, folder, db_session):
    db_session.add(
        Teacher(email="b@example.com", name="B", password_hash=hash_password("x" * 12))
    )
    db_session.commit()
    (folder / archive_name(NOON)).write_bytes(b"x")
    html = page(signed_in)
    assert "flask backup" in html
    assert "Back up now" not in html
    assert "Download latest" not in html
    assert post_backup(signed_in).status_code == 403
    assert signed_in.get("/settings/backups/latest").status_code == 403


def test_backup_job_survives_lock_held(app, signed_in, folder, db_session, monkeypatch):
    called = []
    monkeypatch.setattr(backup, "run_command", lambda *a: called.append(a))
    post_backup(signed_in)
    holder = create_engine(TEST_DATABASE_URL).connect()
    try:
        holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        assert jobs.run_once(app)
    finally:
        holder.rollback()
        holder.close()
    job = backup_jobs(db_session)[0]
    db_session.refresh(job)
    assert job.state == "done"
    assert called == []
    assert list(folder.glob("*.tar.gz")) == []
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
