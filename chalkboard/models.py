from datetime import date, datetime, time

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Table,
    Time,
    UniqueConstraint,
    func,
)
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
    class_times: Mapped[list[ClassTime]] = relationship(
        order_by="ClassTime.weekday", cascade="all, delete-orphan"
    )
    sites: Mapped[list[AllowedSite]] = relationship(
        secondary=subject_sites, order_by="AllowedSite.domain"
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
