from datetime import time

import pytest
from sqlalchemy import func, select

from chalkboard.models import (
    AllowedSite,
    Chunk,
    Citation,
    ClassTime,
    Job,
    PlanVersion,
    Sentence,
    Source,
    Student,
    Subject,
    SyllabusUnit,
    Teacher,
    subject_sites,
)
from chalkboard.passwords import hash_password
from tests.conftest import csrf_token, sign_in

VECTOR = [0.1] * 1024


@pytest.fixture
def data(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.extensions["chalkboard.settings"], "data_dir", tmp_path)
    (tmp_path / "sources").mkdir()
    return tmp_path


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def count(db_session, model):
    return db_session.scalar(select(func.count()).select_from(model))


def make_subject(db_session, teacher, name):
    subject = Subject(
        teacher_id=teacher.id, name=name, class_minutes=60, colour="#0E7A64"
    )
    db_session.add(subject)
    db_session.commit()
    return subject


def fill(db_session, data, subject, site=None):
    """A source with a file and a chunk, a unit, a plan sentence citing the chunk, a student and a class time."""
    source = Source(
        subject_id=subject.id,
        number=1,
        name="notes.txt",
        kind="txt",
        file_path="x",
        passages=1,
    )
    db_session.add(source)
    db_session.flush()
    source.file_path = f"sources/{source.id}.txt"
    (data / source.file_path).write_text("notes")
    chunk = Chunk(source_id=source.id, position=0, text="hello", embedding=VECTOR)
    plan = PlanVersion(subject_id=subject.id, kind="overview", number=1)
    db_session.add_all(
        [
            chunk,
            plan,
            SyllabusUnit(source_id=source.id, position=0, number=1, title="Motion"),
            Student(subject_id=subject.id, name="Leo Martins"),
            ClassTime(subject_id=subject.id, weekday=1, start=time(9)),
        ]
    )
    db_session.flush()
    sentence = Sentence(version_id=plan.id, ordinal=0, text="Things move.")
    db_session.add(sentence)
    db_session.flush()
    db_session.add(
        Citation(sentence_id=sentence.id, chunk_id=chunk.id, source_number=1)
    )
    if site:
        db_session.execute(
            subject_sites.insert().values(
                subject_id=subject.id, allowed_site_id=site.id
            )
        )
    db_session.commit()
    return source


def post(client, url, **data):
    return client.post(url, data={"csrf_token": csrf_token(client, "/settings")} | data)


def test_confirm_subject_shows_counts(signed_in, db_session, data, teacher):
    subject = make_subject(db_session, teacher, "Physics")
    fill(db_session, data, subject)
    html = signed_in.get(
        "/settings/delete/subject", query_string={"subject_id": subject.id}
    ).get_data(as_text=True)
    assert "Delete Physics?" in html
    assert "Its 1 sources, 1 plan versions and 1 students will be removed." in html


def test_delete_subject_removes_rows_and_files(signed_in, db_session, data, teacher):
    site = AllowedSite(teacher_id=teacher.id, domain="nasa.gov")
    db_session.add(site)
    db_session.commit()
    doomed = make_subject(db_session, teacher, "Physics")
    kept = make_subject(db_session, teacher, "Maths")
    gone = fill(db_session, data, doomed, site)
    survivor = fill(db_session, data, kept, site)
    gone_path, survivor_path = gone.file_path, survivor.file_path
    response = post(signed_in, f"/settings/delete/subject/{doomed.id}")
    assert response.status_code == 302
    db_session.expire_all()
    assert [s.name for s in db_session.scalars(select(Subject))] == ["Maths"]
    for model in (Source, Chunk, SyllabusUnit, PlanVersion, Sentence, Citation):
        assert count(db_session, model) == 1
    assert count(db_session, Student) == 1
    assert count(db_session, ClassTime) == 1
    assert count(db_session, AllowedSite) == 1
    assert not (data / gone_path).exists()
    assert (data / survivor_path).exists()


def test_delete_subject_needs_csrf(signed_in, db_session, teacher):
    subject = make_subject(db_session, teacher, "Physics")
    assert signed_in.post(f"/settings/delete/subject/{subject.id}").status_code == 400
    assert count(db_session, Subject) == 1


def test_delete_other_teachers_subject_404(signed_in, db_session, teacher):
    other = Teacher(email="b@example.com", name="B", password_hash=hash_password("x"))
    db_session.add(other)
    db_session.commit()
    theirs = make_subject(db_session, other, "Chemistry")
    assert (
        signed_in.get(
            "/settings/delete/subject", query_string={"subject_id": theirs.id}
        ).status_code
        == 404
    )
    assert post(signed_in, f"/settings/delete/subject/{theirs.id}").status_code == 404
    assert count(db_session, Subject) == 1


def test_delete_everything_needs_DELETE(signed_in, db_session, data, teacher):
    fill(db_session, data, make_subject(db_session, teacher, "Physics"))
    for typed in ("delete", ""):
        response = post(signed_in, "/settings/delete/everything", confirm=typed)
        assert response.status_code == 400
        assert "Type DELETE to confirm" in response.get_data(as_text=True)
    assert count(db_session, Subject) == 1
    assert count(db_session, Source) == 1


def test_delete_everything_leaves_empty_account(signed_in, db_session, data, teacher):
    site = AllowedSite(teacher_id=teacher.id, domain="nasa.gov")
    db_session.add(site)
    db_session.commit()
    mine = [
        fill(db_session, data, make_subject(db_session, teacher, name), site)
        for name in ("Physics", "Maths")
    ]
    other = Teacher(email="b@example.com", name="B", password_hash=hash_password("x"))
    db_session.add(other)
    db_session.commit()
    theirs = make_subject(db_session, other, "Chemistry")
    their_source = fill(db_session, data, theirs)
    mine_paths = [s.file_path for s in mine]
    teacher.theme, teacher.embed_model = "dark", "my-embedder"
    db_session.add_all(
        [
            Job(teacher_id=teacher.id, kind="backup", state="done"),
            Job(teacher_id=teacher.id, kind="backup", state="running"),
            Job(teacher_id=other.id, kind="backup", state="done"),
        ]
    )
    db_session.commit()

    response = post(signed_in, "/settings/delete/everything", confirm="DELETE")
    assert response.status_code == 302
    db_session.expire_all()
    mine_subjects = select(func.count()).select_from(Subject)
    assert db_session.scalar(mine_subjects.where(Subject.teacher_id == teacher.id)) == 0
    assert count(db_session, AllowedSite) == 0
    assert [
        j.state
        for j in db_session.scalars(select(Job).where(Job.teacher_id == teacher.id))
    ] == ["running"]
    assert count(db_session, Teacher) == 2
    assert (teacher.theme, teacher.embed_model) == ("dark", "my-embedder")
    assert count(db_session, Subject) == 1
    assert count(db_session, Source) == 1
    assert count(db_session, Job) == 2
    assert not any((data / p).exists() for p in mine_paths)
    assert (data / their_source.file_path).exists()
    html = signed_in.get("/subjects").get_data(as_text=True)
    assert "Start with your first subject" in html
