"""teachers

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teachers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "theme", sa.String(length=6), server_default="system", nullable=False
        ),
        sa.Column(
            "sidebar_collapsed", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("last_subject_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_teachers"),
        sa.UniqueConstraint("email", name="uq_teachers_email"),
        sa.CheckConstraint(
            "theme IN ('light', 'dark', 'system')", name="ck_teachers_theme"
        ),
    )


def downgrade() -> None:
    op.drop_table("teachers")
