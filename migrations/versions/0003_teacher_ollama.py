"""teacher ollama settings and status

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "teachers", sa.Column("ollama_url", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "teachers", sa.Column("embed_model", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "teachers", sa.Column("write_model", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "teachers",
        sa.Column(
            "ollama_status",
            sa.String(length=13),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_teachers_ollama_status",
        "teachers",
        "ollama_status IN ('unknown', 'ok', 'unreachable', 'model_missing')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_teachers_ollama_status", "teachers", type_="check")
    op.drop_column("teachers", "ollama_status")
    op.drop_column("teachers", "write_model")
    op.drop_column("teachers", "embed_model")
    op.drop_column("teachers", "ollama_url")
