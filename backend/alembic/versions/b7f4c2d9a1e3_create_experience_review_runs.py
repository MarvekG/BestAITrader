"""create experience review runs

Revision ID: b7f4c2d9a1e3
Revises: c18f2a6e9d44
Create Date: 2026-11-27 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7f4c2d9a1e3"
down_revision: Union[str, None] = "c18f2a6e9d44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建经验复盘运行队列表。"""
    op.create_table(
        "experience_review_runs",
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("stock_code", sa.String(length=20), nullable=False),
        sa.Column("review_horizon", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("review_run_id"),
    )
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
        "ix_experience_review_runs_status_created",
        "experience_review_runs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_experience_review_runs_user_stock_status",
        "experience_review_runs",
        ["user_id", "stock_code", "status"],
    )
    op.create_index(
        "ix_experience_review_runs_session_status",
        "experience_review_runs",
        ["session_id", "status"],
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


def downgrade() -> None:
    """删除经验复盘运行队列表。"""
    op.drop_index("uq_experience_review_runs_active_stock", table_name="experience_review_runs")
    op.drop_index("uq_experience_review_runs_active_session", table_name="experience_review_runs")
    op.drop_index("ix_experience_review_runs_session_status", table_name="experience_review_runs")
    op.drop_index("ix_experience_review_runs_user_stock_status", table_name="experience_review_runs")
    op.drop_index("ix_experience_review_runs_status_created", table_name="experience_review_runs")
    op.drop_index("ix_experience_review_runs_created_at", table_name="experience_review_runs")
    op.drop_index("ix_experience_review_runs_status", table_name="experience_review_runs")
    op.drop_index("ix_experience_review_runs_user_id", table_name="experience_review_runs")
    op.drop_index("ix_experience_review_runs_session_id", table_name="experience_review_runs")
    op.drop_table("experience_review_runs")
