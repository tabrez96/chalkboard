from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

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
    # No foreign key yet: the subjects table arrives with the subjects ticket.
    last_subject_id: Mapped[int | None]
    # Null until saved on S08; the CHALKBOARD_* defaults apply meanwhile (ai.config_for).
    ollama_url: Mapped[str | None] = mapped_column(String(255))
    embed_model: Mapped[str | None] = mapped_column(String(100))
    write_model: Mapped[str | None] = mapped_column(String(100))
    # Last known result of talking to the Ollama server; drives the sidebar dot and the banner.
    ollama_status: Mapped[str] = mapped_column(String(13), server_default="unknown")

    __table_args__ = (
        CheckConstraint("theme IN ('light', 'dark', 'system')", name="theme"),
        CheckConstraint(
            "ollama_status IN ('unknown', 'ok', 'unreachable', 'model_missing')",
            name="ollama_status",
        ),
    )
