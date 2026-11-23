import io
from datetime import UTC, datetime

import docx
import pytest
from sqlalchemy import func, select

from chalkboard import jobs, sources
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.base import EMBED_DIMENSIONS, OllamaUnreachable
from chalkboard.ai.fake import FakeOllama
from chalkboard.models import Chunk, Source, Student, Subject
from chalkboard.sources import UploadError, delete_source, status_label, store_upload
from tests.pdfs import make_pdf

LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)
TEXT = " ".join(f"word{i}" for i in range(900)).encode()


@pytest.fixture
def subject(db_session, teacher):
    row = Subject(
        teacher_id=teacher.id, name="Physics", class_minutes=60, colour="#0E7A64"
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def data(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.extensions["chalkboard.settings"], "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def server(app):
    fake = FakeOllama()
    app.extensions[FACTORY_KEY] = lambda config, redactor: RedactingClient(
        fake, redactor
    )
    return fake


def upload(app, db_session, subject, name="notes.txt", content=TEXT):
    with app.app_context():
        return store_upload(db_session, subject, name, io.BytesIO(content))


def docx_bytes():
    buffer = io.BytesIO()
    document = docx.Document()
    for i in range(3):
        document.add_paragraph(" ".join(f"word{i}_{j}" for j in range(40)))
    document.save(buffer)
    return buffer.getvalue()


def chunks_of(db_session, source):
    return list(
        db_session.scalars(
            select(Chunk).where(Chunk.source_id == source.id).order_by(Chunk.position)
        )
    )


def refresh(db_session, source):
    db_session.refresh(source)
    db_session.refresh(source.job)


def test_store_upload_saves_file_row_and_job(app, db_session, subject, data):
    source = upload(app, db_session, subject)
    assert (data / "sources" / f"{source.id}.txt").read_bytes() == TEXT
    assert (source.number, source.origin, source.kind) == (1, "upload", "txt")
    assert source.job.kind == "ingest_source"
    assert status_label(source) == "Reading · 0%"


def test_source_numbers_never_reused(app, db_session, subject, data, teacher):
    made = [upload(app, db_session, subject) for _ in range(3)]
    with app.app_context():
        delete_source(db_session, made[2])
    assert upload(app, db_session, subject).number == 4
    other = Subject(
        teacher_id=teacher.id, name="Maths", class_minutes=45, colour="#3A57C9"
    )
    db_session.add(other)
    db_session.commit()
    assert upload(app, db_session, other).number == 1


def test_store_upload_rejects_large_and_unknown_files(
    app, db_session, subject, data, monkeypatch
):
    monkeypatch.setattr(sources, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(UploadError, match="50 MB"):
        upload(app, db_session, subject, content=b"x" * 11)
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0
    assert list((data / "sources").iterdir()) == []
    db_session.refresh(subject)
    assert subject.source_counter == 0
    with pytest.raises(UploadError):
        upload(app, db_session, subject, name="virus.exe", content=b"x")


def test_store_upload_strips_path_from_name(app, db_session, subject, data):
    a = upload(app, db_session, subject, name="../../etc/passwd.txt")
    b = upload(app, db_session, subject, name="C:\\x\\a  b.txt")
    assert (a.name, b.name) == ("passwd.txt", "a b.txt")
    assert a.file_path == f"sources/{a.id}.txt"


@pytest.mark.parametrize("kind", ["pdf", "docx", "txt", "md"])
def test_ingest_each_kind_ends_ready(app, db_session, subject, data, server, kind):
    content = {
        "pdf": make_pdf(["Alpha one two", "Beta three four"]),
        "docx": docx_bytes(),
        "txt": TEXT,
        "md": TEXT,
    }[kind]
    source = upload(app, db_session, subject, name=f"f.{kind}", content=content)
    assert jobs.run_once(app)
    refresh(db_session, source)
    rows = chunks_of(db_session, source)
    n = len(rows)
    assert n >= 1
    assert status_label(source) == f"Ready · {n} passage{'s' if n != 1 else ''}"
    assert [c.position for c in rows] == list(range(len(rows)))
    assert all(len(c.embedding) == EMBED_DIMENSIONS for c in rows)
    if kind == "txt":
        assert len(rows) >= 2
    if kind == "pdf":
        assert all(c.page_start and c.page_end for c in rows)


def test_ingest_unreachable_waits_then_finishes(app, db_session, subject, data, server):
    server.up = False
    source = upload(app, db_session, subject)
    jobs.run_once(app)
    refresh(db_session, source)
    assert (data / source.file_path).exists()
    assert status_label(source) == "Saved · waiting for Ollama"
    assert chunks_of(db_session, source) == []
    server.up = True
    source.job.run_after = LONG_AGO
    db_session.commit()
    jobs.run_once(app)
    refresh(db_session, source)
    assert status_label(source).startswith("Ready · ")
    assert chunks_of(db_session, source)


def test_ingest_unreachable_mid_embedding_parks(
    app, db_session, subject, data, server, monkeypatch
):
    def down(texts):
        raise OllamaUnreachable("down")

    monkeypatch.setattr(server, "embed", down)
    source = upload(app, db_session, subject)
    jobs.run_once(app)
    refresh(db_session, source)
    assert source.job.state == "waiting_ollama"
    assert chunks_of(db_session, source) == []


@pytest.mark.parametrize(
    ("name", "content"), [("a.txt", b""), ("a.pdf", b"garbage" * 40)]
)
def test_ingest_empty_or_corrupt_file_fails(
    app, db_session, subject, data, server, name, content
):
    source = upload(app, db_session, subject, name=name, content=content)
    jobs.run_once(app)
    refresh(db_session, source)
    assert status_label(source) == "Couldn't read the file"
    assert source.job.attempts == 0


def test_ingest_rerun_replaces_chunks(app, db_session, subject, data, server):
    source = upload(app, db_session, subject)
    jobs.run_once(app)
    refresh(db_session, source)
    before = len(chunks_of(db_session, source))
    source.job.state, source.job.run_after = "queued", LONG_AGO
    db_session.commit()
    jobs.run_once(app)
    refresh(db_session, source)
    assert source.job.state == "done"
    assert len(chunks_of(db_session, source)) == before


def test_ingest_redacts_student_names(app, db_session, subject, data, server):
    subject.students = [Student(name="Leo Martins")]
    db_session.commit()
    upload(app, db_session, subject, content=b"Today Leo Martins presented. " * 20)
    jobs.run_once(app)
    assert server.calls
    assert not any("Leo" in t for call in server.calls for t in call)


def test_delete_source_removes_file_row_and_chunks(
    app, db_session, subject, data, server
):
    source = upload(app, db_session, subject)
    jobs.run_once(app)
    refresh(db_session, source)
    source_id, path = source.id, data / source.file_path
    assert chunks_of(db_session, source)
    with app.app_context():
        delete_source(db_session, source)
    assert not path.exists()
    assert db_session.get(Source, source_id) is None
    assert (
        db_session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.source_id == source_id)
        )
        == 0
    )


def test_delete_waiting_source_removes_its_job(app, db_session, subject, data, server):
    server.up = False
    source = upload(app, db_session, subject)
    jobs.run_once(app)
    job_id = source.job_id
    with app.app_context():
        delete_source(db_session, source)
    assert db_session.get(jobs.Job, job_id) is None


def test_ingest_of_deleted_source_is_noop(app, db_session, subject, data, server):
    job = jobs.enqueue(
        db_session, subject.teacher_id, "ingest_source", {"source_id": 0}
    )
    db_session.commit()
    jobs.run_once(app)
    db_session.refresh(job)
    assert job.state == "done"


def test_status_label_singular():
    assert status_label(Source(passages=1, job=None)) == "Ready · 1 passage"


def test_embedding_dimension_matches_model():
    assert Chunk.__table__.c.embedding.type.dim == EMBED_DIMENSIONS
