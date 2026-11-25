from datetime import date, datetime, time
from decimal import Decimal

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
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
    # At most one per subject (uq_sources_syllabus). The number is kept and only hidden while this is set.
    is_syllabus: Mapped[bool] = mapped_column(server_default=sa.false())
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    job: Mapped[Job | None] = relationship()

    __table_args__ = (
        UniqueConstraint("subject_id", "number"),
        CheckConstraint("kind IN ('pdf', 'docx', 'txt', 'md', 'web')", name="kind"),
        Index(
            "uq_sources_syllabus",
            "subject_id",
            unique=True,
            postgresql_where=sa.text("is_syllabus"),
        ),
    )


class SyllabusUnit(Base):
    __tablename__ = "syllabus_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    # 0-based order in the syllabus.
    position: Mapped[int]
    # As printed ("Unit IV" is 4); typed units are numbered 1, 2, 3… in the order typed.
    number: Mapped[int]
    title: Mapped[str] = mapped_column(String(200))
    # Typed by the teacher; detection never replaces typed units.
    typed: Mapped[bool] = mapped_column(server_default=sa.false())
    # Taught in another term: listed on S04 Coverage as "Not this term" and never flagged.
    other_term: Mapped[bool] = mapped_column(server_default=sa.false())

    __table_args__ = (UniqueConstraint("source_id", "position"),)


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


PLAN_KINDS = ("overview", "chapter", "lessons")
ITEM_KINDS = ("chapter", "section", "lesson")


class PlanVersion(Base):
    __tablename__ = "plan_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    kind: Mapped[str] = mapped_column(String(8))
    # Identifies the chapter a chapter draft belongs to; None for the overview.
    chapter_key: Mapped[str | None] = mapped_column(String(40))
    # 1, 2, … per (subject, kind, chapter_key).
    number: Mapped[int]
    # CASCADE, not SET NULL: a SET NULL would be an UPDATE, which the content trigger rejects.
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE")
    )
    # The teacher's request text that produced this version; "" for a first draft.
    change_request: Mapped[str] = mapped_column(Text, server_default="")
    # The job that wrote it. The only column that may change, and only to NULL (job deleted).
    status_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    items: Mapped[list[PlanItem]] = relationship(
        order_by="PlanItem.ordinal", cascade="all, delete-orphan", passive_deletes=True
    )
    sentences: Mapped[list[Sentence]] = relationship(
        order_by="Sentence.ordinal", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("kind IN ('overview', 'chapter', 'lessons')", name="kind"),
        CheckConstraint(
            "(kind = 'overview') = (chapter_key IS NULL)", name="chapter_key"
        ),
        CheckConstraint("number >= 1", name="number"),
        UniqueConstraint(
            "subject_id",
            "kind",
            "chapter_key",
            "number",
            postgresql_nulls_not_distinct=True,
        ),
    )


class PlanItem(Base):
    __tablename__ = "plan_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE"), index=True
    )
    # 0-based order within the version.
    ordinal: Mapped[int]
    kind: Mapped[str] = mapped_column(String(7))
    title: Mapped[str] = mapped_column(String(200))
    hours_estimate: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    unit_number: Mapped[int | None]
    notes: Mapped[str] = mapped_column(Text, server_default="")

    __table_args__ = (
        CheckConstraint("kind IN ('chapter', 'section', 'lesson')", name="kind"),
        UniqueConstraint("version_id", "ordinal"),
    )


class Sentence(Base):
    __tablename__ = "sentences"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_items.id", ondelete="CASCADE")
    )
    # 0-based order within the version.
    ordinal: Mapped[int]
    # Minutes of a timed lesson step; None for other plans.
    minutes: Mapped[int | None]
    text: Mapped[str] = mapped_column(Text)
    # Set by the checker: a cited passage does not support the sentence ("Check this").
    check_flag: Mapped[bool] = mapped_column(server_default=sa.false())
    check_reason: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[Citation]] = relationship(
        order_by="Citation.id", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("minutes IS NULL OR minutes >= 1", name="minutes"),
        UniqueConstraint("version_id", "ordinal"),
    )


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(primary_key=True)
    sentence_id: Mapped[int] = mapped_column(
        ForeignKey("sentences.id", ondelete="CASCADE"), index=True
    )
    # None once the source is deleted; shown as "source deleted".
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), index=True
    )
    # Kept so a deleted source can still be named.
    source_number: Mapped[int]

    __table_args__ = (UniqueConstraint("sentence_id", "chunk_id"),)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    # A separate row, so versions stay immutable.
    version_id: Mapped[int] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE"), unique=True
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AddedChapter(Base):
    __tablename__ = "added_chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # A chapter the overview missed; listed after the overview's chapters, oldest first. Key "a<id>".
    title: Mapped[str] = mapped_column(String(200))
    hours: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    notes: Mapped[str] = mapped_column(Text, server_default="")


CHAT_ROLES = ("teacher", "assistant")
CHAT_LABELS = ("", "from_sources", "not_in_sources")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    # The lesson (a PlanItem of kind 'lesson') the chat belongs to.
    item_id: Mapped[int] = mapped_column(
        ForeignKey("plan_items.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    role: Mapped[str] = mapped_column(String(9))
    # "sources" for now; "web" is added with web search.
    scope: Mapped[str] = mapped_column(String(7), server_default="sources")
    # Stored as typed; RedactingClient removes names only on the way to the model.
    text: Mapped[str] = mapped_column(Text)
    # "" for the teacher's questions.
    label: Mapped[str] = mapped_column(String(14), server_default="")
    # Answer sentences: [{"text": str, "cites": [[chunk_id, source_number], ...]}]; [] otherwise.
    parts: Mapped[list] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    # On a question: the job writing its answer.
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    # On an answer: the question it answers (one answer per question).
    question_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), unique=True
    )

    __table_args__ = (
        CheckConstraint("role IN ('teacher', 'assistant')", name="role"),
        CheckConstraint("scope IN ('sources')", name="scope"),
        CheckConstraint(
            "label IN ('', 'from_sources', 'not_in_sources')", name="label"
        ),
    )
