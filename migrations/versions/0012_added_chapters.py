"""chapters the teacher added to the course plan

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "added_chapters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("hours", sa.Numeric(4, 1), nullable=True),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_added_chapters"),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_added_chapters_subject_id_subjects",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_added_chapters_subject_id", "added_chapters", ["subject_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("added_chapters")
