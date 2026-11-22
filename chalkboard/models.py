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

    __table_args__ = (
        CheckConstraint("theme IN ('light', 'dark', 'system')", name="theme"),
    )
