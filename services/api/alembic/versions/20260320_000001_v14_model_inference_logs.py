"""v14: model inference logs for ML A/B analytics

Revision ID: 20260320_000001
Revises: 20260318_000006
Create Date: 2026-03-20 00:00:01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260320_000001"
down_revision = "20260318_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_inference_logs",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=False), nullable=False),
        sa.Column("player_id", UUID(as_uuid=False), nullable=True),
        sa.Column("model_id", UUID(as_uuid=False), nullable=True),
        sa.Column("model_variant", sa.String(length=20), nullable=False, server_default="champion"),
        sa.Column("anomaly_score", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("is_anomaly", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_id"], ["model_registry.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_model_inference_logs_tenant_created_at",
        "model_inference_logs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "idx_model_inference_logs_model_created_at",
        "model_inference_logs",
        ["model_id", "created_at"],
    )
    op.create_index(
        "idx_model_inference_logs_request_id",
        "model_inference_logs",
        ["tenant_id", "request_id"],
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_model_inference_logs_request_id", table_name="model_inference_logs")
    op.drop_index("idx_model_inference_logs_model_created_at", table_name="model_inference_logs")
    op.drop_index("idx_model_inference_logs_tenant_created_at", table_name="model_inference_logs")
    op.drop_table("model_inference_logs")
