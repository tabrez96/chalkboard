"""Sources: storing uploads, the ingest job that turns them into embedded passages, and their status."""

import os
from pathlib import Path
from typing import BinaryIO

from flask import current_app
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.chunker import chunk
from chalkboard.extract import ExtractError, extract, kind_for
from chalkboard.jobs import JobContext
from chalkboard.models import Chunk, Source, Subject

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
EMBED_BATCH = 16
UNREADABLE = "Couldn't read the file"
WAITING = "Saved · waiting for Ollama"


class UploadError(ValueError):
    """The message is shown to the teacher."""


def data_dir() -> Path:
    return current_app.extensions["chalkboard.settings"].data_dir


def clean_name(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return " ".join(base.split())[:255] or "Untitled"


def next_number(session: Session, subject: Subject) -> int:
    # The UPDATE locks the subject row, so concurrent uploads get different numbers; the counter never goes down.
    return session.execute(
        update(Subject)
        .where(Subject.id == subject.id)
        .values(source_counter=Subject.source_counter + 1)
        .returning(Subject.source_counter)
    ).scalar_one()


def store_upload(
    session: Session, subject: Subject, filename: str, stream: BinaryIO
) -> Source:
    kind = kind_for(filename)
    if kind is None:
        raise UploadError("Upload a PDF, Word (.docx), text or Markdown file")
    source = Source(
        subject_id=subject.id,
        number=next_number(session, subject),
        name=clean_name(filename),
        kind=kind,
        file_path="",
    )
    session.add(source)
    session.flush()
    source.file_path = f"sources/{source.id}.{kind}"
    target = data_dir() / source.file_path
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with part.open("wb") as out:
            while block := stream.read(1024 * 1024):
                size += len(block)
                if size > MAX_UPLOAD_BYTES:
                    raise UploadError("The file is larger than 50 MB")
                out.write(block)
    except BaseException:
        part.unlink(missing_ok=True)
        session.rollback()
        raise
    try:
        os.replace(part, target)
        source.job = jobs.enqueue(
            session, subject.teacher_id, "ingest_source", {"source_id": source.id}
        )
        session.commit()
    except BaseException:
        part.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        session.rollback()
        raise
    return source


def delete_source(session: Session, source: Source) -> None:
    path = data_dir() / source.file_path
    # A running job is left alone: its handler does nothing once the row is gone.
    if source.job is not None and source.job.state in ("queued", "waiting_ollama"):
        session.delete(source.job)
    # Chunks go with the row through the foreign key's ON DELETE CASCADE.
    session.delete(source)
    session.commit()
    path.unlink(missing_ok=True)


def status_label(source: Source) -> str:
    job = source.job
    if job is None or job.state == "done":
        n = source.passages
        return f"Ready · {n} passage{'s' if n != 1 else ''}"
    if job.state == "failed":
        return UNREADABLE
    if job.state == "waiting_ollama":
        return WAITING
    return f"Reading · {job.progress}%"


@jobs.register("ingest_source")
def ingest_source(ctx: JobContext) -> None:
    source = ctx.session.get(Source, ctx.payload["source_id"])
    if source is None:
        return
    ctx.progress("Reading the file", 0)
    try:
        sections = extract(data_dir() / source.file_path, source.kind)
    except (ExtractError, OSError) as exc:
        raise jobs.JobFailed(UNREADABLE) from exc
    pieces = chunk(sections)
    if not pieces:
        raise jobs.JobFailed(UNREADABLE)
    subject = ctx.session.get(Subject, source.subject_id)
    client = model_client(ctx.teacher, subject)
    vectors = []
    try:
        for i in range(0, len(pieces), EMBED_BATCH):
            batch = pieces[i : i + EMBED_BATCH]
            vectors.extend(client.embed([p.text for p in batch]))
            ctx.progress("Reading the file", 10 + 90 * len(vectors) // len(pieces))
    finally:
        client.close()
    # Chunks are written only now, so an outage halfway leaves nothing behind and re-runs stay idempotent.
    ctx.session.execute(delete(Chunk).where(Chunk.source_id == source.id))
    ctx.session.add_all(
        Chunk(
            source_id=source.id,
            position=p.position,
            page_start=p.page_start,
            page_end=p.page_end,
            text=p.text,
            embedding=v,
        )
        for p, v in zip(pieces, vectors, strict=True)
    )
    source.passages = len(pieces)
