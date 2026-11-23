from datetime import date, datetime, time

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chalkboard.db import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Stored stripped and lower-cased (passwords.normalise_email); the unique constraint relies on it.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    theme: Mapped[str] = mapped_column(String(6), server_default="system")
    sidebar_collapsed: Mapped[bool] = mapped_column(server_default=sa.false())
    last_subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL", use_alter=True)
    )
    # Null until saved on S08; the CHALKBOARD_* defaults apply meanwhile (ai.config_for).
    ollama_url: Mapped[str | None] = mapped_column(String(255))
    embed_model: Mapped[str | None] = mapped_column(String(100))
    write_model: Mapped[str | None] = mapped_column(String(100))
    # Last known result of talking to the Ollama server; drives the sidebar dot and the banner.
    ollama_status: Mapped[str] = mapped_column(String(13), server_default="unknown")
    # Open mode on S08: any public website may be saved as a source.
    allow_any_site: Mapped[bool] = mapped_column(server_default=sa.false())

    __table_args__ = (
        CheckConstraint("theme IN ('light', 'dark', 'system')", name="theme"),
        CheckConstraint(
            "ollama_status IN ('unknown', 'ok', 'unreachable', 'model_missing')",
            name="ollama_status",
        ),
    )


class AllowedSite(Base):
    __tablename__ = "allowed_sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE")
    )
    # Normalised by sites.normalise_domain; matches the host and all its subdomains.
    domain: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("teacher_id", "domain"),)


SUBJECT_COLOURS = (
    "#0E7A64",
    "#3A57C9",
    "#B4492B",
    "#7A4FB5",
    "#A8326E",
    "#2F6F8F",
)
BOARDS = ("cbse", "icse", "state", "ib", "none")

subject_sites = Table(
    "subject_sites",
    Base.metadata,
    Column(
        "subject_id", ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "allowed_site_id",
        ForeignKey("allowed_sites.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    name: Mapped[str] = mapped_column(String(80))
    grade: Mapped[str] = mapped_column(String(40), server_default="")
    class_minutes: Mapped[int]
    board: Mapped[str] = mapped_column(String(5), server_default="none")
    # Only set when board is "state", e.g. "Maharashtra State Board".
    state_board: Mapped[str | None] = mapped_column(String(100))
    term_start: Mapped[date | None]
    term_end: Mapped[date | None]
    colour: Mapped[str] = mapped_column(String(7))
    # "uploads": sources are uploads only; "web": pages from the ticked sites (or any site in open mode).
    web_access: Mapped[str] = mapped_column(String(7), server_default="uploads")
    # Last source number handed out; numbers are never reused, so old citations never point at another file.
    source_counter: Mapped[int] = mapped_column(server_default="0")
    class_times: Mapped[list[ClassTime]] = relationship(
        order_by="ClassTime.weekday", cascade="all, delete-orphan"
    )
    sites: Mapped[list[AllowedSite]] = relationship(
        secondary=subject_sites, order_by="AllowedSite.domain"
    )
    students: Mapped[list[Student]] = relationship(
        order_by="Student.id", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("class_minutes BETWEEN 10 AND 240", name="class_minutes"),
        CheckConstraint(
            "board IN ('cbse', 'icse', 'state', 'ib', 'none')", name="board"
        ),
        CheckConstraint("web_access IN ('uploads', 'web')", name="web_access"),
        CheckConstraint(
            "colour IN ('#0E7A64', '#3A57C9', '#B4492B', '#7A4FB5', '#A8326E', '#2F6F8F')",
            name="colour",
        ),
        CheckConstraint(
            "(term_start IS NULL) = (term_end IS NULL) AND (term_end IS NULL OR term_end >= term_start)",
            name="term",
        ),
    )


class ClassTime(Base):
    __tablename__ = "class_times"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE")
    )
    weekday: Mapped[int] = mapped_column(SmallInteger)  # 0 = Monday … 5 = Saturday
    start: Mapped[time] = mapped_column(Time)

    __table_args__ = (
        UniqueConstraint("subject_id", "weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 5", name="weekday"),
    )


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Never sent to a model: ai.RedactingClient replaces it before every request.
    name: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(String(200), server_default="")


JOB_STATES = ("queued", "running", "waiting_ollama", "done", "failed")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    state: Mapped[str] = mapped_column(String(14), server_default="queued")
    # Label of the current step, e.g. "Reading pages"; shown with the percent.
    step: Mapped[str] = mapped_column(String(80), server_default="")
    progress: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    # Why the job is waiting or failed; written for the teacher, shown as is.
    error: Mapped[str | None] = mapped_column(String(500))
    # Failed runs so far (Ollama waits don't count).
    attempts: Mapped[int] = mapped_column(server_default="0")
    # Bumped on every claim; a worker whose number is out of date lost the job and must not write to it.
    claims: Mapped[int] = mapped_column(server_default="0")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Refreshed on claim and on every progress update; a running job with an old heartbeat lost its worker.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'waiting_ollama', 'done', 'failed')",
            name="state",
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress"),
    )


SOURCE_KINDS = ("pdf", "docx", "txt", "md", "web")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    number: Mapped[int]
    # The original file name, as shown to the teacher.
    name: Mapped[str] = mapped_column(String(255))
    # "upload" for uploads; the site's domain for saved pages.
    origin: Mapped[str] = mapped_column(String(253), server_default="upload")
    kind: Mapped[str] = mapped_column(String(4))
    # Relative to Settings.data_dir, e.g. "sources/12.pdf".
    file_path: Mapped[str] = mapped_column(String(255))
    passages: Mapped[int] = mapped_column(server_default="0")
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    job: Mapped[Job | None] = relationship()

    __table_args__ = (
        UniqueConstraint("subject_id", "number"),
        CheckConstraint("kind IN ('pdf', 'docx', 'txt', 'md', 'web')", name="kind"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    # 0-based order within the source.
    position: Mapped[int]
    # 1-based PDF pages; None for formats without pages.
    page_start: Mapped[int | None]
    page_end: Mapped[int | None]
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(1024)
    )  # ai.base.EMBED_DIMENSIONS

    __table_args__ = (
        UniqueConstraint("source_id", "position"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
