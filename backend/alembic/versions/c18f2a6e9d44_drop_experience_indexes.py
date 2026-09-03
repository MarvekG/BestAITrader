"""drop experience indexes

Revision ID: c18f2a6e9d44
Revises: 8dde1ec39dcf
Create Date: 2026-09-03 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c18f2a6e9d44"
down_revision: Union[str, None] = "8dde1ec39dcf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除已经由单文档记忆替代的经验索引表。"""
    op.drop_table("experience_indexes")


def downgrade() -> None:
    """恢复经验索引表结构；已删除的历史索引数据无法恢复。"""
    op.create_table(
        "experience_indexes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.String(length=100), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("stock_code", sa.String(length=20), nullable=True),
        sa.Column("stock_name", sa.String(length=100), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("strategy", sa.String(length=100), nullable=True),
        sa.Column("review_horizon", sa.String(length=10), nullable=True),
        sa.Column("outcome_label", sa.String(length=50), nullable=True),
        sa.Column("correctness", sa.String(length=50), nullable=True),
        sa.Column("importance", sa.String(length=20), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "memory_id", name="uq_experience_indexes_user_memory_id"),
    )
    for column in (
        "correctness",
        "created_at",
        "importance",
        "industry",
        "memory_id",
        "outcome_label",
        "review_horizon",
        "review_run_id",
        "session_id",
        "stock_code",
        "strategy",
        "user_id",
    ):
        op.create_index(
            f"ix_experience_indexes_{column}",
            "experience_indexes",
            [column],
            unique=False,
        )
