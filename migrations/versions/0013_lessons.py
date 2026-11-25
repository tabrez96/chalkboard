"""lesson plans: the 'lessons' plan kind and timed sentences

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


# 0010 passed full names to create_table, so the naming convention prefixed them twice.
OLD_KIND_CHECK = op.f("ck_plan_versions_ck_plan_versions_kind")


def upgrade() -> None:
    # Short names for the new constraints: the naming convention adds the ck_<table>_ prefix.
    op.drop_constraint(OLD_KIND_CHECK, "plan_versions", type_="check")
    op.create_check_constraint(
        "kind", "plan_versions", "kind IN ('overview', 'chapter', 'lessons')"
    )
    op.add_column("sentences", sa.Column("minutes", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "minutes", "sentences", "minutes IS NULL OR minutes >= 1"
    )


def downgrade() -> None:
    # DELETE is allowed; the insert-only trigger only blocks UPDATE.
    op.execute("DELETE FROM plan_versions WHERE kind = 'lessons'")
    op.drop_constraint("minutes", "sentences", type_="check")
    op.drop_column("sentences", "minutes")
    op.drop_constraint("kind", "plan_versions", type_="check")
    op.create_check_constraint(
        OLD_KIND_CHECK, "plan_versions", "kind IN ('overview', 'chapter')"
    )
