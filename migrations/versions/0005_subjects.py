"""subjects, class times and the sites each subject may use

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("grade", sa.String(length=40), server_default="", nullable=False),
        sa.Column("class_minutes", sa.Integer(), nullable=False),
        sa.Column("board", sa.String(length=5), server_default="none", nullable=False),
        sa.Column("state_board", sa.String(length=100), nullable=True),
        sa.Column("term_start", sa.Date(), nullable=True),
        sa.Column("term_end", sa.Date(), nullable=True),
        sa.Column("colour", sa.String(length=7), nullable=False),
        sa.Column(
            "web_access", sa.String(length=7), server_default="uploads", nullable=False
        ),
        sa.CheckConstraint(
            "class_minutes BETWEEN 10 AND 240", name="ck_subjects_class_minutes"
        ),
        sa.CheckConstraint(
            "board IN ('cbse', 'icse', 'state', 'ib', 'none')", name="ck_subjects_board"
        ),
        sa.CheckConstraint(
            "web_access IN ('uploads', 'web')", name="ck_subjects_web_access"
        ),
        sa.CheckConstraint(
            "colour IN ('#0E7A64', '#3A57C9', '#B4492B', '#7A4FB5', '#A8326E', '#2F6F8F')",
            name="ck_subjects_colour",
        ),
        sa.CheckConstraint(
            "(term_start IS NULL) = (term_end IS NULL) AND (term_end IS NULL OR term_end >= term_start)",
            name="ck_subjects_term",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teachers.id"],
            name="fk_subjects_teacher_id_teachers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_subjects"),
    )
    op.create_table(
        "class_times",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start", sa.Time(), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 5", name="ck_class_times_weekday"),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_class_times_subject_id_subjects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_class_times"),
        sa.UniqueConstraint("subject_id", "weekday", name="uq_class_times_subject_id"),
    )
    op.create_table(
        "subject_sites",
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("allowed_site_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_subject_sites_subject_id_subjects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["allowed_site_id"],
            ["allowed_sites.id"],
            name="fk_subject_sites_allowed_site_id_allowed_sites",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "subject_id", "allowed_site_id", name="pk_subject_sites"
        ),
    )
    # No subjects exist yet, so any stored id is stale.
    op.execute("UPDATE teachers SET last_subject_id = NULL")
    op.create_foreign_key(
        "fk_teachers_last_subject_id_subjects",
        "teachers",
        "subjects",
        ["last_subject_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_teachers_last_subject_id_subjects", "teachers", type_="foreignkey"
    )
    op.drop_table("subject_sites")
    op.drop_table("class_times")
    op.drop_table("subjects")
