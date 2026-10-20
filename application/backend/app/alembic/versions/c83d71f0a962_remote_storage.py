# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Add project remote storage and image references."""

import sqlalchemy as sa
from alembic import op

revision = "c83d71f0a962"
down_revision = "b7e2d94a1f3c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add storage connections and nullable remote references."""
    op.create_table(
        "remote_storages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "project_id", sa.Text(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.add_column("media", sa.Column("remote", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove remote storage metadata."""
    op.drop_column("media", "remote")
    op.drop_table("remote_storages")
