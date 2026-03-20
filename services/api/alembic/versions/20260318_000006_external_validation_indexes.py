"""external validation indexes for idempotency lookup

Revision ID: 20260318_000006
Revises: 20260318_000005
Create Date: 2026-03-18 00:00:06
"""
from __future__ import annotations

from alembic import op

revision = "20260318_000006"
down_revision = "20260318_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_ext_validation_idempotency_lookup",
        "external_validation_requests",
        ["tenant_id", "player_id", "provider", "validation_type", "requested_at"],
    )
    op.create_index(
        "idx_ext_validation_status_requested_at",
        "external_validation_requests",
        ["status", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_ext_validation_status_requested_at", table_name="external_validation_requests")
    op.drop_index("idx_ext_validation_idempotency_lookup", table_name="external_validation_requests")
