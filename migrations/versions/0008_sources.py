"""sources and their chunks

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subjects",
        sa.Column("source_counter", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "origin", sa.String(length=253), server_default="upload", nullable=False
        ),
        sa.Column("kind", sa.String(length=4), nullable=False),
        sa.Column("file_path", sa.String(length=255), nullable=False),
        sa.Column("passages", sa.Integer(), server_default="0", nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('pdf', 'docx', 'txt', 'md', 'web')", name="ck_sources_kind"
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_sources_subject_id_subjects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_sources_job_id_jobs", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.UniqueConstraint("subject_id", "number", name="uq_sources_subject_id"),
    )
    op.create_index("ix_sources_subject_id", "sources", ["subject_id"])
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_chunks_source_id_sources",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.UniqueConstraint("source_id", "position", name="uq_chunks_source_id"),
    )
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_embedding", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_sources_subject_id", table_name="sources")
    op.drop_table("sources")
    op.drop_column("subjects", "source_counter")
