"""web search setting and web answers in the assistant chat

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "teachers",
        sa.Column(
            "web_search_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    # Short constraint names: the naming convention adds the ck_<table>_ prefix.
    op.drop_constraint("scope", "chat_messages", type_="check")
    op.create_check_constraint("scope", "chat_messages", "scope IN ('sources', 'web')")
    op.drop_constraint("label", "chat_messages", type_="check")
    op.create_check_constraint(
        "label",
        "chat_messages",
        "label IN ('', 'from_sources', 'not_in_sources', 'from_web')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM chat_messages WHERE scope = 'web'")
    op.drop_constraint("label", "chat_messages", type_="check")
    op.create_check_constraint(
        "label", "chat_messages", "label IN ('', 'from_sources', 'not_in_sources')"
    )
    op.drop_constraint("scope", "chat_messages", type_="check")
    op.create_check_constraint("scope", "chat_messages", "scope IN ('sources')")
    op.drop_column("teachers", "web_search_enabled")
