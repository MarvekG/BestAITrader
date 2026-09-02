"""create memory_documents

Revision ID: 8dde1ec39dcf
Revises: 749d3c60cc58
Create Date: 2026-09-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8dde1ec39dcf'
down_revision: Union[str, None] = '749d3c60cc58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用数据库结构变更。"""
    op.create_table('memory_documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('size_chars', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'stock_code', name='uq_memory_documents_user_stock')
    )
    op.create_index(op.f('ix_memory_documents_user_id'), 'memory_documents', ['user_id'], unique=False)


def downgrade() -> None:
    """回滚数据库结构变更。"""
    op.drop_index(op.f('ix_memory_documents_user_id'), table_name='memory_documents')
    op.drop_table('memory_documents')
