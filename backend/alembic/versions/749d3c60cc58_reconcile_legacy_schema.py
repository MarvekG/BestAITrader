"""reconcile legacy schema

Revision ID: 749d3c60cc58
Revises: a5513c3e027a
Create Date: 2026-08-22 08:27:08.687858
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '749d3c60cc58'
down_revision: Union[str, None] = 'a5513c3e027a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用数据库结构变更。"""
    # 由当前 ORM metadata 与历史主库结构的差异生成，并经人工审阅。
    op.alter_column('async_tasks', 'user_id',
               existing_type=sa.INTEGER(),
               comment='Owner User ID',
               existing_nullable=True)
    op.drop_index(op.f('ix_experience_indexes_memory_observation_id'), table_name='experience_indexes')
    op.create_index(op.f('ix_experience_indexes_memory_id'), 'experience_indexes', ['memory_id'], unique=False)
    op.alter_column('llm_usage_logs', 'cache_miss_tokens',
               existing_type=sa.INTEGER(),
               server_default=None,
               existing_nullable=True)
    op.alter_column('orders', 'status',
               existing_type=postgresql.ENUM('pending', 'filled', 'cancelled', 'rejected', name='order_status'),
               server_default=None,
               existing_nullable=True)
    op.alter_column('sessions', 'source',
               existing_type=sa.VARCHAR(length=20),
               server_default=None,
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_enabled',
               existing_type=sa.BOOLEAN(),
               server_default=None,
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_frequency',
               existing_type=sa.VARCHAR(length=20),
               server_default=None,
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_time',
               existing_type=sa.VARCHAR(length=5),
               server_default=None,
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_trading_frequency',
               existing_type=sa.VARCHAR(length=50),
               server_default=None,
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_trading_strategy',
               existing_type=sa.VARCHAR(length=50),
               server_default=None,
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_run_immediately',
               existing_type=sa.BOOLEAN(),
               server_default=None,
               existing_nullable=False)
    op.alter_column('system_settings', 'id',
               existing_type=sa.BIGINT(),
               type_=sa.Integer(),
               existing_nullable=False)
    op.create_index(op.f('ix_system_settings_user_id'), 'system_settings', ['user_id'], unique=False)
    op.alter_column('dragon_tiger_data', 'details',
               existing_type=postgresql.JSON(astext_type=sa.Text()),
               type_=postgresql.JSONB(astext_type=sa.Text()),
               existing_nullable=True,
               schema='data')
    op.alter_column('research_messages', 'payload',
               existing_type=postgresql.JSON(astext_type=sa.Text()),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_messages', 'status',
               existing_type=sa.VARCHAR(length=20),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_messages', 'visible_to_user',
               existing_type=sa.BOOLEAN(),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_messages', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_messages_message_type'), table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_messages_parent_message_id'), table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_messages_run_id'), table_name='research_messages', schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_messages_message_type'), 'research_messages', ['message_type'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_messages_parent_message_id'), 'research_messages', ['parent_message_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_messages_run_id'), 'research_messages', ['run_id'], unique=False, schema='stock_picker_interactive')
    op.alter_column('research_runs', 'status',
               existing_type=sa.VARCHAR(length=50),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'current_stage',
               existing_type=sa.VARCHAR(length=50),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'current_phase',
               existing_type=sa.VARCHAR(length=30),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'checkpoint_payload',
               existing_type=postgresql.JSON(astext_type=sa.Text()),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'cache_context_version',
               existing_type=sa.VARCHAR(length=80),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'version',
               existing_type=sa.INTEGER(),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'updated_at',
               existing_type=postgresql.TIMESTAMP(),
               server_default=None,
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_approved_plan_artifact_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_current_phase'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_execution_lease_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_latest_checkpoint_artifact_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_latest_result_artifact_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_lease_expires_at'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_pending_message_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_status'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_interactive_research_runs_user_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_current_phase'), 'research_runs', ['current_phase'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_pending_message_id'), 'research_runs', ['pending_message_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_status'), 'research_runs', ['status'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_user_id'), 'research_runs', ['user_id'], unique=False, schema='stock_picker_interactive')
    op.drop_column('research_runs', 'execution_lease_id', schema='stock_picker_interactive')
    op.drop_column('research_runs', 'approved_plan_artifact_id', schema='stock_picker_interactive')
    op.drop_column('research_runs', 'latest_result_artifact_id', schema='stock_picker_interactive')
    op.drop_column('research_runs', 'lease_expires_at', schema='stock_picker_interactive')
    op.drop_column('research_runs', 'latest_checkpoint_artifact_id', schema='stock_picker_interactive')


def downgrade() -> None:
    """回滚数据库结构变更。"""
    # 删除的旧列无法恢复其历史值，只恢复旧结构。
    op.add_column('research_runs', sa.Column('latest_checkpoint_artifact_id', sa.UUID(), autoincrement=False, nullable=True), schema='stock_picker_interactive')
    op.add_column('research_runs', sa.Column('lease_expires_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True), schema='stock_picker_interactive')
    op.add_column('research_runs', sa.Column('latest_result_artifact_id', sa.UUID(), autoincrement=False, nullable=True), schema='stock_picker_interactive')
    op.add_column('research_runs', sa.Column('approved_plan_artifact_id', sa.UUID(), autoincrement=False, nullable=True), schema='stock_picker_interactive')
    op.add_column('research_runs', sa.Column('execution_lease_id', sa.VARCHAR(length=80), autoincrement=False, nullable=True), schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_user_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_status'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_pending_message_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_current_phase'), table_name='research_runs', schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_user_id'), 'research_runs', ['user_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_status'), 'research_runs', ['status'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_pending_message_id'), 'research_runs', ['pending_message_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_lease_expires_at'), 'research_runs', ['lease_expires_at'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_latest_result_artifact_id'), 'research_runs', ['latest_result_artifact_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_latest_checkpoint_artifact_id'), 'research_runs', ['latest_checkpoint_artifact_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_execution_lease_id'), 'research_runs', ['execution_lease_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_current_phase'), 'research_runs', ['current_phase'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_research_runs_approved_plan_artifact_id'), 'research_runs', ['approved_plan_artifact_id'], unique=False, schema='stock_picker_interactive')
    op.alter_column('research_runs', 'updated_at',
               existing_type=postgresql.TIMESTAMP(),
               server_default=sa.text('CURRENT_TIMESTAMP'),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               server_default=sa.text('CURRENT_TIMESTAMP'),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'version',
               existing_type=sa.INTEGER(),
               server_default=sa.text('1'),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'cache_context_version',
               existing_type=sa.VARCHAR(length=80),
               server_default=sa.text("'research-agent-v1'::character varying"),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'checkpoint_payload',
               existing_type=postgresql.JSON(astext_type=sa.Text()),
               server_default=sa.text("'{}'::json"),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'current_phase',
               existing_type=sa.VARCHAR(length=30),
               server_default=sa.text("'planning'::character varying"),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'current_stage',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'drafting_plan'::character varying"),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_runs', 'status',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'drafting_plan'::character varying"),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_messages_run_id'), table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_messages_parent_message_id'), table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_messages_message_type'), table_name='research_messages', schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_messages_run_id'), 'research_messages', ['run_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_messages_parent_message_id'), 'research_messages', ['parent_message_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_interactive_messages_message_type'), 'research_messages', ['message_type'], unique=False, schema='stock_picker_interactive')
    op.alter_column('research_messages', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               server_default=sa.text('CURRENT_TIMESTAMP'),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_messages', 'visible_to_user',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('true'),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_messages', 'status',
               existing_type=sa.VARCHAR(length=20),
               server_default=sa.text("'completed'::character varying"),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('research_messages', 'payload',
               existing_type=postgresql.JSON(astext_type=sa.Text()),
               server_default=sa.text("'{}'::json"),
               existing_nullable=False,
               schema='stock_picker_interactive')
    op.alter_column('dragon_tiger_data', 'details',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               type_=postgresql.JSON(astext_type=sa.Text()),
               existing_nullable=True,
               schema='data')
    op.drop_index(op.f('ix_system_settings_user_id'), table_name='system_settings')
    op.alter_column('system_settings', 'id',
               existing_type=sa.Integer(),
               type_=sa.BIGINT(),
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_run_immediately',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_trading_strategy',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'价值投资 (Value Investing)'::character varying"),
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_trading_frequency',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'中长线持有 (Position Trading)'::character varying"),
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_time',
               existing_type=sa.VARCHAR(length=5),
               server_default=sa.text("'09:35'::character varying"),
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_frequency',
               existing_type=sa.VARCHAR(length=20),
               server_default=sa.text("'daily'::character varying"),
               existing_nullable=False)
    op.alter_column('stock_warehouse', 'auto_analysis_enabled',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False)
    op.alter_column('sessions', 'source',
               existing_type=sa.VARCHAR(length=20),
               server_default=sa.text("'manual'::character varying"),
               existing_nullable=False)
    op.alter_column('orders', 'status',
               existing_type=postgresql.ENUM('pending', 'filled', 'cancelled', 'rejected', name='order_status'),
               server_default=sa.text("'pending'::order_status"),
               existing_nullable=True)
    op.alter_column('llm_usage_logs', 'cache_miss_tokens',
               existing_type=sa.INTEGER(),
               server_default=sa.text('0'),
               existing_nullable=True)
    op.drop_index(op.f('ix_experience_indexes_memory_id'), table_name='experience_indexes')
    op.create_index(op.f('ix_experience_indexes_memory_observation_id'), 'experience_indexes', ['memory_id'], unique=False)
    op.alter_column('async_tasks', 'user_id',
               existing_type=sa.INTEGER(),
               comment=None,
               existing_comment='Owner User ID',
               existing_nullable=True)
