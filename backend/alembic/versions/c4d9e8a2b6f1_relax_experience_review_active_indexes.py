"""relax experience review active indexes

Revision ID: c4d9e8a2b6f1
Revises: b7f4c2d9a1e3
Create Date: 2026-11-27 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d9e8a2b6f1"
down_revision: Union[str, None] = "b7f4c2d9a1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """允许同股票多条 queued，仅限制同股票同时 running。"""
    op.drop_index("uq_experience_review_runs_active_stock", table_name="experience_review_runs")
    op.drop_index("uq_experience_review_runs_active_session", table_name="experience_review_runs")
    for index_name in (
        "ix_experience_review_runs_session_id",
        "ix_experience_review_runs_user_id",
        "ix_experience_review_runs_status",
        "ix_experience_review_runs_created_at",
        "ix_experience_review_runs_session_status",
        "ix_experience_review_runs_user_stock_status",
    ):
        op.drop_index(index_name, table_name="experience_review_runs")
    op.create_index(
        "uq_experience_review_runs_active_task",
        "experience_review_runs",
        ["session_id", "review_horizon"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "uq_experience_review_runs_running_stock",
        "experience_review_runs",
        ["user_id", "stock_code"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    """恢复旧的活动 session / 股票唯一约束和辅助索引。"""
    op.drop_index("uq_experience_review_runs_running_stock", table_name="experience_review_runs")
    op.drop_index("uq_experience_review_runs_active_task", table_name="experience_review_runs")
    op.create_index(
        "ix_experience_review_runs_session_id",
        "experience_review_runs",
        ["session_id"],
    )
    op.create_index(
        "ix_experience_review_runs_user_id",
        "experience_review_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_experience_review_runs_status",
        "experience_review_runs",
        ["status"],
    )
    op.create_index(
        "ix_experience_review_runs_created_at",
        "experience_review_runs",
        ["created_at"],
    )
    op.create_index(
        "ix_experience_review_runs_session_status",
        "experience_review_runs",
        ["session_id", "status"],
    )
    op.create_index(
        "ix_experience_review_runs_user_stock_status",
        "experience_review_runs",
        ["user_id", "stock_code", "status"],
    )
    op.create_index(
        "uq_experience_review_runs_active_session",
        "experience_review_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "uq_experience_review_runs_active_stock",
        "experience_review_runs",
        ["user_id", "stock_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
