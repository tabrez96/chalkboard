import pytest
from sqlalchemy import func, select

from chalkboard import jobs
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeEmbedder, FakeOllama
from chalkboard.models import Chunk, Job, Source, Subject, Teacher
from chalkboard.passwords import hash_password
from tests.conftest import csrf_token, sign_in

ZERO = [0.0] * 1024
NEW_MODEL = "other-embed:1b"


@pytest.fixture
def server(app):
    fake = FakeOllama()
    fake.configs = []

    def factory(config, redactor):
        fake.configs.append(config)
        return RedactingClient(fake, redactor)

    app.extensions[FACTORY_KEY] = factory
    return fake


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def make_subject(db_session, teacher, name="Physics"):
    subject = Subject(
        teacher_id=teacher.id, name=name, class_minutes=60, colour="#0E7A64"
    )
    db_session.add(subject)
    db_session.commit()
    return subject


def make_source(db_session, subject, number=1, texts=("alpha", "beta"), job=None):
    source = Source(
        subject_id=subject.id,
        number=number,
        name=f"n{number}.txt",
        kind="txt",
        file_path=f"sources/{number}.txt",
        passages=len(texts),
        job_id=job.id if job else None,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add_all(
        Chunk(source_id=source.id, position=i, text=t, embedding=ZERO)
        for i, t in enumerate(texts)
    )
    db_session.commit()
    return source


def save(client, confirm=None, **values):
    data = {
        "ollama_url": "http://localhost:11434",
        "embed_model": NEW_MODEL,
        "write_model": "qwen3.5:9b",
        "action": "save",
        "csrf_token": csrf_token(client, "/settings"),
    } | values
    if confirm:
        data["confirm"] = confirm
    return client.post("/settings/ollama", data=data)


def reembed_jobs(db_session):
    return db_session.scalars(select(Job).where(Job.kind == "reembed_all")).all()


def test_changing_embed_model_asks_first(signed_in, db_session, teacher):
    make_source(db_session, make_subject(db_session, teacher))
    response = save(signed_in)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Change the embedding model?" in html
    assert 'name="confirm" value="1"' in html
    db_session.refresh(teacher)
    assert teacher.embed_model != NEW_MODEL
    assert reembed_jobs(db_session) == []


def test_confirmed_change_queues_reembed(signed_in, db_session, teacher):
    subject = make_subject(db_session, teacher)
    ready = make_source(db_session, subject)
    busy_job = Job(teacher_id=teacher.id, kind="ingest_source", state="queued")
    db_session.add(busy_job)
    db_session.flush()
    busy = make_source(db_session, subject, number=2, job=busy_job)
    assert save(signed_in, confirm="1").status_code == 302
    db_session.refresh(teacher)
    db_session.refresh(ready)
    db_session.refresh(busy)
    assert teacher.embed_model == NEW_MODEL
    (job,) = reembed_jobs(db_session)
    assert ready.job_id == job.id
    assert busy.job_id == busy_job.id
    live = signed_in.get(f"/subjects/{subject.id}/sources/live")
    assert "Reading · 0%" in live.get_data(as_text=True)


def test_same_model_or_no_chunks_saves_directly(signed_in, db_session, teacher):
    # No chunks yet: a new model is saved without asking.
    response = save(signed_in)
    assert response.status_code == 302
    db_session.refresh(teacher)
    assert teacher.embed_model == NEW_MODEL
    assert reembed_jobs(db_session) == []
    # Same model again, now with chunks: nothing to ask.
    make_source(db_session, make_subject(db_session, teacher))
    assert save(signed_in).status_code == 302
    assert reembed_jobs(db_session) == []


def test_reembed_all_rewrites_every_chunk(app, signed_in, db_session, teacher, server):
    first = make_source(db_session, make_subject(db_session, teacher, "Physics"))
    second = make_source(
        db_session, make_subject(db_session, teacher, "Maths"), texts=("gamma",)
    )
    other = Teacher(email="b@example.com", name="B", password_hash=hash_password("x"))
    db_session.add(other)
    db_session.commit()
    theirs = make_source(db_session, make_subject(db_session, other, "Chem"))
    save(signed_in, confirm="1")
    server.configs.clear()
    assert jobs.run_once(app)
    db_session.expire_all()
    assert server.configs
    assert {config.embed_model for config in server.configs} == {NEW_MODEL}
    embedder = FakeEmbedder()
    for source in (first, second):
        for chunk in db_session.scalars(
            select(Chunk).where(Chunk.source_id == source.id)
        ):
            assert list(chunk.embedding) == pytest.approx(
                embedder.embed([chunk.text])[0], abs=1e-6
            )
    for chunk in db_session.scalars(select(Chunk).where(Chunk.source_id == theirs.id)):
        assert list(chunk.embedding) == ZERO
    (job,) = reembed_jobs(db_session)
    assert (job.state, job.progress) == ("done", 100)
    assert db_session.scalar(select(func.count()).select_from(Chunk)) == 5


def test_reembed_parks_when_ollama_down(app, signed_in, db_session, teacher, server):
    source = make_source(db_session, make_subject(db_session, teacher))
    save(signed_in, confirm="1")
    server.up = False
    jobs.run_once(app)
    db_session.expire_all()
    (job,) = reembed_jobs(db_session)
    assert job.state == "waiting_ollama"
    for chunk in db_session.scalars(select(Chunk).where(Chunk.source_id == source.id)):
        assert list(chunk.embedding) == ZERO


def test_failed_reembed_sources_join_the_next_run(signed_in, db_session, teacher):
    subject = make_subject(db_session, teacher)
    failed = Job(teacher_id=teacher.id, kind="reembed_all", state="failed")
    db_session.add(failed)
    db_session.flush()
    stuck = make_source(db_session, subject, job=failed)
    assert save(signed_in, confirm="1").status_code == 302
    db_session.refresh(stuck)
    job = max(reembed_jobs(db_session), key=lambda j: j.id)
    assert job.id != failed.id
    assert stuck.job_id == job.id


def test_deleting_one_source_keeps_the_shared_reembed_job(
    app, signed_in, db_session, teacher, monkeypatch, tmp_path
):
    from chalkboard.sources import delete_source

    monkeypatch.setattr(app.extensions["chalkboard.settings"], "data_dir", tmp_path)
    subject = make_subject(db_session, teacher)
    first = make_source(db_session, subject)
    second = make_source(db_session, subject, number=2)
    assert save(signed_in, confirm="1").status_code == 302
    db_session.refresh(first)
    db_session.refresh(second)
    job_id = first.job_id
    assert job_id is not None
    with app.app_context():
        delete_source(db_session, first)
    db_session.refresh(second)
    assert db_session.get(Job, job_id) is not None
    assert second.job_id == job_id
