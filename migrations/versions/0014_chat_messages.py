"""assistant chat messages per lesson

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Short constraint names: the naming convention adds the ck_<table>_ prefix.
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=9), nullable=False),
        sa.Column(
            "scope", sa.String(length=7), server_default="sources", nullable=False
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=14), server_default="", nullable=False),
        sa.Column(
            "parts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("question_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("role IN ('teacher', 'assistant')", name="role"),
        sa.CheckConstraint("scope IN ('sources')", name="scope"),
        sa.CheckConstraint(
            "label IN ('', 'from_sources', 'not_in_sources')", name="label"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chat_messages"),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_chat_messages_subject_id_subjects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["plan_items.id"],
            name="fk_chat_messages_item_id_plan_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_chat_messages_job_id_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["chat_messages.id"],
            name="fk_chat_messages_question_id_chat_messages",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("question_id", name="uq_chat_messages_question_id"),
    )
    op.create_index(
        "ix_chat_messages_subject_id", "chat_messages", ["subject_id"], unique=False
    )
    op.create_index(
        "ix_chat_messages_item_id", "chat_messages", ["item_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
