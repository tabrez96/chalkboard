"""trivia sets per lesson, one row per version

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Short constraint name: the naming convention adds the ck_<table>_ prefix.
    op.create_table(
        "trivia_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "questions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("number >= 1", name="number"),
        sa.PrimaryKeyConstraint("id", name="pk_trivia_sets"),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_trivia_sets_subject_id_subjects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["plan_items.id"],
            name="fk_trivia_sets_item_id_plan_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_trivia_sets_job_id_jobs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("item_id", "number", name="uq_trivia_sets_item_id_number"),
        sa.UniqueConstraint("job_id", name="uq_trivia_sets_job_id"),
    )
    op.create_index(
        "ix_trivia_sets_subject_id", "trivia_sets", ["subject_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("trivia_sets")
