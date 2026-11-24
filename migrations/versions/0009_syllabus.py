"""syllabus flag on sources and the syllabus units

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "is_syllabus", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_index(
        "uq_sources_syllabus",
        "sources",
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text("is_syllabus"),
    )
    op.create_table(
        "syllabus_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("typed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_syllabus_units_source_id_sources",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_syllabus_units"),
        sa.UniqueConstraint(
            "source_id", "position", name="uq_syllabus_units_source_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("syllabus_units")
    op.drop_index("uq_sources_syllabus", table_name="sources")
    op.drop_column("sources", "is_syllabus")
