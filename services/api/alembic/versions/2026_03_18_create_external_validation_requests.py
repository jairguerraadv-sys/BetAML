"""create external validation requests table

Revision ID: 20260318_000005
Revises: 20260314_000004
Create Date: 2026-03-18 00:00:05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260318_000005"
down_revision = "20260314_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'external_validation_requests',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=False), nullable=False),
        sa.Column('player_id', UUID(as_uuid=False), nullable=False),
        sa.Column('provider', sa.String(40), nullable=False, server_default='mock_identity'),
        sa.Column('validation_type', sa.String(40), nullable=False, server_default='CPF_IDENTITY'),
        sa.Column('status', sa.String(20), nullable=False, server_default='PENDING'),
        sa.Column('request_payload', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('response_payload', JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column('external_request_id', sa.Text()),
        sa.Column('error_message', sa.Text()),
        sa.Column('requested_by', UUID(as_uuid=False)),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()')),
    )
    op.create_index('ix_external_validation_requests_player_id', 'external_validation_requests', ['player_id'])
    op.create_index('ix_external_validation_requests_tenant_id', 'external_validation_requests', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_external_validation_requests_player_id', table_name='external_validation_requests')
    op.drop_index('ix_external_validation_requests_tenant_id', table_name='external_validation_requests')
    op.drop_table('external_validation_requests')
