"""allowed websites and open mode

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "allowed_sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teachers.id"],
            name="fk_allowed_sites_teacher_id_teachers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_allowed_sites"),
        sa.UniqueConstraint("teacher_id", "domain", name="uq_allowed_sites_teacher_id"),
    )
    op.add_column(
        "teachers",
        sa.Column(
            "allow_any_site",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("teachers", "allow_any_site")
    op.drop_table("allowed_sites")
