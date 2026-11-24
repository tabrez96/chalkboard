"""versioned plans, items, sentences, citations and approvals

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

REJECT_FUNCTION = """
CREATE FUNCTION reject_content_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    clearable text[] := coalesce(TG_ARGV, '{}');
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
    col text;
BEGIN
    -- Trigger arguments name columns that may change, and only to NULL (ON DELETE SET NULL).
    FOREACH col IN ARRAY clearable LOOP
        IF new_row -> col <> 'null'::jsonb AND new_row -> col IS DISTINCT FROM old_row -> col THEN
            RAISE EXCEPTION 'Plan content is insert-only: write a new version (%.%)', TG_TABLE_NAME, col;
        END IF;
    END LOOP;
    IF (new_row - clearable) IS DISTINCT FROM (old_row - clearable) THEN
        RAISE EXCEPTION 'Plan content is insert-only: write a new version (%)', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END $$
"""

REQUIRE_FUNCTION = """
CREATE FUNCTION require_citation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM sentences WHERE id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM citations WHERE sentence_id = NEW.id AND chunk_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Sentence % has no citation', NEW.id;
    END IF;
    RETURN NULL;
END $$
"""

INSERT_ONLY = (
    ("plan_versions", "'status_job_id'"),
    ("plan_items", ""),
    ("sentences", ""),
    ("citations", "'chunk_id'"),
    ("approvals", ""),
)


def upgrade() -> None:
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("chapter_key", sa.String(length=40), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Integer(), nullable=True),
        sa.Column("change_request", sa.Text(), server_default="", nullable=False),
        sa.Column("status_job_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('overview', 'chapter')", name="ck_plan_versions_kind"
        ),
        sa.CheckConstraint(
            "(kind = 'overview') = (chapter_key IS NULL)",
            name="ck_plan_versions_chapter_key",
        ),
        sa.CheckConstraint("number >= 1", name="ck_plan_versions_number"),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_plan_versions_subject_id_subjects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["plan_versions.id"],
            name="fk_plan_versions_parent_version_id_plan_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["status_job_id"],
            ["jobs.id"],
            name="fk_plan_versions_status_job_id_jobs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plan_versions"),
        sa.UniqueConstraint(
            "subject_id",
            "kind",
            "chapter_key",
            "number",
            name="uq_plan_versions_subject_id",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_plan_versions_subject_id", "plan_versions", ["subject_id"], unique=False
    )
    op.create_table(
        "plan_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=7), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("hours_estimate", sa.Numeric(4, 1), nullable=True),
        sa.Column("unit_number", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint(
            "kind IN ('chapter', 'section', 'lesson')", name="ck_plan_items_kind"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["plan_versions.id"],
            name="fk_plan_items_version_id_plan_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plan_items"),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_plan_items_version_id"),
    )
    op.create_index(
        "ix_plan_items_version_id", "plan_items", ["version_id"], unique=False
    )
    op.create_table(
        "sentences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "check_flag", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("check_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["plan_versions.id"],
            name="fk_sentences_version_id_plan_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["plan_items.id"],
            name="fk_sentences_item_id_plan_items",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sentences"),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_sentences_version_id"),
    )
    op.create_index(
        "ix_sentences_version_id", "sentences", ["version_id"], unique=False
    )
    op.create_table(
        "citations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sentence_id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=True),
        sa.Column("source_number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sentence_id"],
            ["sentences.id"],
            name="fk_citations_sentence_id_sentences",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name="fk_citations_chunk_id_chunks",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_citations"),
        sa.UniqueConstraint("sentence_id", "chunk_id", name="uq_citations_sentence_id"),
    )
    op.create_index(
        "ix_citations_sentence_id", "citations", ["sentence_id"], unique=False
    )
    op.create_index("ix_citations_chunk_id", "citations", ["chunk_id"], unique=False)
    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["plan_versions.id"],
            name="fk_approvals_version_id_plan_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.UniqueConstraint("version_id", name="uq_approvals_version_id"),
    )
    op.execute(REJECT_FUNCTION)
    for table, args in INSERT_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_content_update({args})"
        )
    op.execute(REQUIRE_FUNCTION)
    op.execute(
        "CREATE CONSTRAINT TRIGGER sentences_need_citation AFTER INSERT ON sentences "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_citation()"
    )


def downgrade() -> None:
    op.drop_table("approvals")
    op.drop_table("citations")
    op.drop_table("sentences")
    op.drop_table("plan_items")
    op.drop_table("plan_versions")
    op.execute("DROP FUNCTION require_citation()")
    op.execute("DROP FUNCTION reject_content_update()")
