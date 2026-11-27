"""Sources: storing uploads and saved pages, the jobs that save a page and turn files into embedded passages, and their status."""

import os
from pathlib import Path
from typing import BinaryIO

from flask import current_app
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.chunker import chunk
from chalkboard.extract import ExtractError, extract, kind_for, page_text
from chalkboard.jobs import JobContext
from chalkboard.models import Chunk, Source, Subject
from chalkboard.net import PageBlocked, PageError, fetch_page, resolve_host
from chalkboard.sites import page_origin, site_access

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# One source file plus the multipart overhead; Flask refuses bigger requests with 413.
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024
EMBED_BATCH = 16
UNREADABLE = "Couldn't read the file"
BLOCKED = "Blocked: site not chosen"
# Tests put a MockTransport and a fake resolver here; production leaves them unset.
PAGE_TRANSPORT_KEY = "chalkboard.page_transport"
PAGE_RESOLVER_KEY = "chalkboard.page_resolver"
WAITING = "Saved · waiting for Ollama"
UNSUPPORTED = "Upload a PDF, Word (.docx), text or Markdown file"


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
        raise UploadError(UNSUPPORTED)
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


def store_page(session: Session, subject: Subject, url: str) -> Source:
    """Record a page to save; the `save_page` job downloads it, so nothing is fetched here."""
    try:
        source = Source(
            subject_id=subject.id,
            number=next_number(session, subject),
            name=clean_name(url),
            origin=page_origin(url),
            kind="web",
            file_path="",
        )
        session.add(source)
        session.flush()
        source.file_path = f"sources/{source.id}.txt"
        source.job = jobs.enqueue(
            session,
            subject.teacher_id,
            "save_page",
            {"source_id": source.id, "url": url},
        )
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return source


def delete_source(session: Session, source: Source) -> None:
    path = data_dir() / source.file_path
    # A running job is left alone: its handler does nothing once the row is gone.
    # A re-embedding job is shared by every source, so it keeps running for the others.
    job = source.job
    if (
        job is not None
        and job.kind != "reembed_all"
        and job.state in ("queued", "waiting_ollama")
    ):
        session.delete(source.job)
    # Chunks go with the row through the foreign key's ON DELETE CASCADE.
    session.delete(source)
    session.commit()
    path.unlink(missing_ok=True)


def affected_plans(session: Session, source: Source) -> list[str]:
    """Titles of approved plans that cite this source; their citations will show "source deleted"."""
    # Plans don't exist yet; the plans ticket fills this in.
    return []


def status_label(source: Source) -> str:
    job = source.job
    if job is None or job.state == "done":
        n = source.passages
        return f"Ready · {n} passage{'s' if n != 1 else ''}"
    if job.state == "failed":
        return BLOCKED if job.error == BLOCKED else UNREADABLE
    if job.state == "waiting_ollama":
        return WAITING
    return f"Reading · {job.progress}%"


def status_tone(source: Source) -> str:
    state = source.job.state if source.job is not None else "done"
    return {"waiting_ollama": "chip-flag", "failed": "chip-danger"}.get(
        state, "chip-ok"
    )


@jobs.register("save_page", needs_ollama=False)
def save_page(ctx: JobContext) -> None:
    source = ctx.session.get(Source, ctx.payload["source_id"])
    if source is None:
        return
    target = data_dir() / source.file_path
    if (
        not target.exists()
    ):  # downloaded once: a re-run after a crash never fetches again
        ctx.progress("Saving the page", 0)
        subject = ctx.session.get(Subject, source.subject_id)
        try:
            page = fetch_page(
                ctx.payload["url"],
                site_access(subject, ctx.teacher),
                transport=current_app.extensions.get(PAGE_TRANSPORT_KEY),
                resolve=current_app.extensions.get(PAGE_RESOLVER_KEY, resolve_host),
            )
        except PageBlocked as exc:
            raise jobs.JobFailed(BLOCKED) from exc
        except PageError as exc:
            raise jobs.JobFailed(UNREADABLE) from exc
        title, text = page_text(page.body, page.content_type, page.url, page.charset)
        if not text.strip():
            raise jobs.JobFailed(UNREADABLE)
        part = target.with_name(target.name + ".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            part.write_text(text, encoding="utf-8")
            os.replace(part, target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        # A delete that committed while the page downloaded found no file to remove; read committed state, not the
        # cached row. A delete that commits after this check unlinks the file itself.
        if ctx.session.scalar(select(Source.id).where(Source.id == source.id)) is None:
            target.unlink(missing_ok=True)
            return
        if title:
            source.name = title[:255]
    source.job = jobs.enqueue(
        ctx.session, ctx.teacher.id, "ingest_source", {"source_id": source.id}
    )


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
    # The committed flag, read under the row lock `use_as_syllabus` takes: one of the two sides queues detection.
    if ctx.session.scalar(
        select(Source.is_syllabus).where(Source.id == source.id).with_for_update()
    ):
        source.job = jobs.enqueue(
            ctx.session, ctx.teacher.id, "detect_units", {"source_id": source.id}
        )
