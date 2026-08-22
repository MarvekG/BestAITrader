"""baseline current ORM schema

Revision ID: a5513c3e027a
Revises:
Create Date: 2026-08-21 22:16:20.856141
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a5513c3e027a'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用数据库结构变更。"""
    op.execute("CREATE SCHEMA IF NOT EXISTS data")
    op.execute("CREATE SCHEMA IF NOT EXISTS stock_picker_interactive")

    # 以下结构由当前 ORM metadata 生成，并经过人工校正。
    op.create_table('api_registry',
    sa.Column('api_name', sa.String(length=100), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('storage_mode', sa.String(length=50), nullable=True),
    sa.Column('target_table', sa.String(length=100), nullable=True),
    sa.Column('update_strategy', sa.String(length=50), nullable=True),
    sa.Column('dedup_keys', sa.ARRAY(sa.Text()), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('last_updated_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('sync_enabled', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('api_name'),
    schema='data'
    )
    op.create_table('common_data',
    sa.Column('api_name', sa.String(length=100), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('update_date', sa.Date(), nullable=False),
    sa.Column('data_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('data_source', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('api_name', 'stock_code', 'update_date'),
    schema='data'
    )
    op.create_table('index_daily',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('index_code', sa.String(length=20), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('open', sa.Float(), nullable=True),
    sa.Column('high', sa.Float(), nullable=True),
    sa.Column('low', sa.Float(), nullable=True),
    sa.Column('close', sa.Float(), nullable=True),
    sa.Column('volume', sa.Float(), nullable=True),
    sa.Column('amount', sa.Float(), nullable=True),
    sa.Column('change', sa.Float(), nullable=True),
    sa.Column('pct_chg', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('index_code', 'trade_date', name='idx_index_daily_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_index_daily_index_code'), 'index_daily', ['index_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_index_daily_trade_date'), 'index_daily', ['trade_date'], unique=False, schema='data')
    op.create_table('industry_data',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('board_code', sa.String(length=20), nullable=False),
    sa.Column('board_name', sa.String(length=100), nullable=True),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('latest_price', sa.Float(), nullable=True),
    sa.Column('change_amount', sa.Float(), nullable=True),
    sa.Column('change_percent', sa.Float(), nullable=True),
    sa.Column('total_market_cap', sa.Float(), nullable=True),
    sa.Column('turnover_rate', sa.Float(), nullable=True),
    sa.Column('rising_stocks_count', sa.Integer(), nullable=True),
    sa.Column('falling_stocks_count', sa.Integer(), nullable=True),
    sa.Column('leading_stock_name', sa.String(length=100), nullable=True),
    sa.Column('leading_stock_change_percent', sa.Float(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    schema='data'
    )
    op.create_index(op.f('ix_data_industry_data_board_code'), 'industry_data', ['board_code'], unique=True, schema='data')
    op.create_index(op.f('ix_data_industry_data_board_name'), 'industry_data', ['board_name'], unique=False, schema='data')
    op.create_table('sector_money_flow',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('sector_name', sa.String(length=100), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('net_inflow', sa.Float(), nullable=True),
    sa.Column('net_inflow_rate', sa.Float(), nullable=True),
    sa.Column('main_net_inflow', sa.Float(), nullable=True),
    sa.Column('huge_net_inflow', sa.Float(), nullable=True),
    sa.Column('huge_net_inflow_rate', sa.Float(), nullable=True),
    sa.Column('large_net_inflow', sa.Float(), nullable=True),
    sa.Column('large_net_inflow_rate', sa.Float(), nullable=True),
    sa.Column('medium_net_inflow', sa.Float(), nullable=True),
    sa.Column('medium_net_inflow_rate', sa.Float(), nullable=True),
    sa.Column('small_net_inflow', sa.Float(), nullable=True),
    sa.Column('small_net_inflow_rate', sa.Float(), nullable=True),
    sa.Column('close_price', sa.Float(), nullable=True),
    sa.Column('change_percent', sa.Float(), nullable=True),
    sa.Column('leading_stock', sa.String(length=100), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sector_name', 'trade_date', name='idx_sector_flow_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_sector_money_flow_sector_name'), 'sector_money_flow', ['sector_name'], unique=False, schema='data')
    op.create_index(op.f('ix_data_sector_money_flow_trade_date'), 'sector_money_flow', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_basic',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('industry', sa.String(length=100), nullable=True),
    sa.Column('sector', sa.String(length=100), nullable=True),
    sa.Column('area', sa.String(length=100), nullable=True),
    sa.Column('list_date', sa.Date(), nullable=True),
    sa.Column('market', sa.String(length=50), nullable=True),
    sa.Column('total_share', sa.Float(), nullable=True),
    sa.Column('float_share', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_basic_stock_code'), 'stock_basic', ['stock_code'], unique=True, schema='data')
    op.create_table('llm_usage_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('role', sa.String(length=50), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('total_tokens', sa.Integer(), nullable=True),
    sa.Column('cached_tokens', sa.Integer(), nullable=True),
    sa.Column('cache_miss_tokens', sa.Integer(), nullable=True),
    sa.Column('reasoning_tokens', sa.Integer(), nullable=True),
    sa.Column('workflow', sa.String(length=50), nullable=True),
    sa.Column('stage', sa.String(length=80), nullable=True),
    sa.Column('call_kind', sa.String(length=50), nullable=True),
    sa.Column('iteration_index', sa.Integer(), nullable=True),
    sa.Column('cache_lane', sa.String(length=50), nullable=True),
    sa.Column('api_key_alias', sa.String(length=80), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_llm_usage_logs_api_key_alias'), 'llm_usage_logs', ['api_key_alias'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_cache_lane'), 'llm_usage_logs', ['cache_lane'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_call_kind'), 'llm_usage_logs', ['call_kind'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_created_at'), 'llm_usage_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_model'), 'llm_usage_logs', ['model'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_role'), 'llm_usage_logs', ['role'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_session_id'), 'llm_usage_logs', ['session_id'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_stage'), 'llm_usage_logs', ['stage'], unique=False)
    op.create_index(op.f('ix_llm_usage_logs_workflow'), 'llm_usage_logs', ['workflow'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('email', sa.String(length=100), nullable=False),
    sa.Column('password_hash', sa.String(length=100), nullable=False),
    sa.Column('full_name', sa.String(length=100), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('is_superuser', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('accounts',
    sa.Column('account_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('total_assets', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('available_cash', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('frozen_cash', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('market_value', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('initial_capital', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('total_profit_loss', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('profit_loss_pct', sa.DECIMAL(precision=5, scale=2), nullable=True),
    sa.Column('total_trades', sa.Integer(), nullable=True),
    sa.Column('win_rate', sa.DECIMAL(precision=5, scale=2), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('account_id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('async_tasks',
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True, comment='Owner User ID'),
    sa.Column('task_name', sa.String(length=255), nullable=False, comment='Task Name'),
    sa.Column('task_type', sa.String(length=100), nullable=False, comment='Task Type'),
    sa.Column('status', sa.String(length=20), nullable=False, comment='Task Status: pending/running/completed/failed'),
    sa.Column('allow_concurrent', sa.Boolean(), nullable=True, comment='Whether to allow concurrency'),
    sa.Column('parameters', sa.JSON(), nullable=True, comment='Task Parameters'),
    sa.Column('result', sa.JSON(), nullable=True, comment='Task Result'),
    sa.Column('error_message', sa.Text(), nullable=True, comment='Error Message'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True, comment='Creation Time'),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True, comment='Start Time'),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True, comment='Completion Time'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('task_id')
    )
    op.create_index(op.f('ix_async_tasks_user_id'), 'async_tasks', ['user_id'], unique=False)
    op.create_table('dragon_tiger_data',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('stock_name', sa.String(length=100), nullable=True),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('net_buy_amount', sa.Float(), nullable=True),
    sa.Column('buy_amount', sa.Float(), nullable=True),
    sa.Column('sell_amount', sa.Float(), nullable=True),
    sa.Column('price_change_percent', sa.Float(), nullable=True),
    sa.Column('listing_reason', sa.String(length=500), nullable=True),
    sa.Column('sequence_number', sa.Integer(), nullable=True),
    sa.Column('interpretation', sa.Text(), nullable=True),
    sa.Column('close_price', sa.Float(), nullable=True),
    sa.Column('total_trade_amount', sa.Float(), nullable=True),
    sa.Column('market_total_trade_amount', sa.Float(), nullable=True),
    sa.Column('net_buy_ratio', sa.Float(), nullable=True),
    sa.Column('trade_amount_ratio', sa.Float(), nullable=True),
    sa.Column('turnover_rate', sa.Float(), nullable=True),
    sa.Column('floating_market_capitalization', sa.Float(), nullable=True),
    sa.Column('post_1_day_price_change_percent', sa.Float(), nullable=True),
    sa.Column('post_2_day_price_change_percent', sa.Float(), nullable=True),
    sa.Column('post_5_day_price_change_percent', sa.Float(), nullable=True),
    sa.Column('post_10_day_price_change_percent', sa.Float(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'trade_date', 'listing_reason', name='idx_dragon_tiger_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_dragon_tiger_data_stock_code'), 'dragon_tiger_data', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_dragon_tiger_data_trade_date'), 'dragon_tiger_data', ['trade_date'], unique=False, schema='data')
    op.create_table('financial_calendar',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('report_period', sa.String(length=20), nullable=False),
    sa.Column('first_book_date', sa.Date(), nullable=True),
    sa.Column('second_book_date', sa.Date(), nullable=True),
    sa.Column('third_book_date', sa.Date(), nullable=True),
    sa.Column('actual_date', sa.Date(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'report_period', name='idx_fin_cal_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_financial_calendar_actual_date'), 'financial_calendar', ['actual_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_financial_calendar_stock_code'), 'financial_calendar', ['stock_code'], unique=False, schema='data')
    op.create_table('kline_data',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('open', sa.Float(), nullable=True),
    sa.Column('close', sa.Float(), nullable=True),
    sa.Column('high', sa.Float(), nullable=True),
    sa.Column('low', sa.Float(), nullable=True),
    sa.Column('volume', sa.Float(), nullable=True),
    sa.Column('turnover', sa.Float(), nullable=True),
    sa.Column('change', sa.Float(), nullable=True),
    sa.Column('change_percent', sa.Float(), nullable=True),
    sa.Column('freq', sa.String(length=10), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'date', 'freq', name='idx_kline_stock_date_freq_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_kline_data_date'), 'kline_data', ['date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_kline_data_stock_code'), 'kline_data', ['stock_code'], unique=False, schema='data')
    op.create_table('northbound_data',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=True),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('hold_shares', sa.Float(), nullable=True),
    sa.Column('hold_value', sa.Float(), nullable=True),
    sa.Column('hold_ratio', sa.Float(), nullable=True),
    sa.Column('close_price', sa.Float(), nullable=True),
    sa.Column('change_percent', sa.Float(), nullable=True),
    sa.Column('net_buy_volume', sa.Float(), nullable=True),
    sa.Column('net_buy_amount', sa.Float(), nullable=True),
    sa.Column('hold_value_change', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'date', name='idx_northbound_stock_date_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_northbound_data_date'), 'northbound_data', ['date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_northbound_data_stock_code'), 'northbound_data', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_block_trade',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('price', sa.Float(), nullable=True),
    sa.Column('volume', sa.Float(), nullable=True),
    sa.Column('amount', sa.Float(), nullable=True),
    sa.Column('premium_rate', sa.Float(), nullable=True),
    sa.Column('buyer', sa.String(length=200), nullable=True),
    sa.Column('seller', sa.String(length=200), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'trade_date', 'buyer', 'seller', 'price', 'volume', name='idx_block_trade_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_block_trade_stock_code'), 'stock_block_trade', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_block_trade_trade_date'), 'stock_block_trade', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_fund_holding',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('report_date', sa.Date(), nullable=False),
    sa.Column('fund_code', sa.String(length=20), nullable=True),
    sa.Column('fund_name', sa.String(length=200), nullable=True),
    sa.Column('hold_amount', sa.Float(), nullable=True),
    sa.Column('hold_market_value', sa.Float(), nullable=True),
    sa.Column('hold_ratio_stock', sa.Float(), nullable=True),
    sa.Column('hold_ratio_fund', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'report_date', 'fund_code', name='idx_fund_holding_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_fund_holding_report_date'), 'stock_fund_holding', ['report_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_fund_holding_stock_code'), 'stock_fund_holding', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_hot_rank',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('rank_change', sa.Integer(), nullable=True),
    sa.Column('hot_value', sa.Float(), nullable=True),
    sa.Column('rank_type', sa.String(length=20), nullable=True),
    sa.Column('current_rank', sa.Integer(), nullable=True),
    sa.Column('new_fans', sa.Float(), nullable=True),
    sa.Column('irons_fans', sa.Float(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema='data'
    )
    op.create_index('idx_hot_rank_query', 'stock_hot_rank', ['stock_code', 'timestamp', 'rank_type'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_hot_rank_stock_code'), 'stock_hot_rank', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_hot_rank_timestamp'), 'stock_hot_rank', ['timestamp'], unique=False, schema='data')
    op.create_table('stock_indicators',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('ma5', sa.Float(), nullable=True),
    sa.Column('ma10', sa.Float(), nullable=True),
    sa.Column('ma20', sa.Float(), nullable=True),
    sa.Column('ma30', sa.Float(), nullable=True),
    sa.Column('ma60', sa.Float(), nullable=True),
    sa.Column('ma120', sa.Float(), nullable=True),
    sa.Column('ma250', sa.Float(), nullable=True),
    sa.Column('macd', sa.Float(), nullable=True),
    sa.Column('macd_signal', sa.Float(), nullable=True),
    sa.Column('macd_hist', sa.Float(), nullable=True),
    sa.Column('kdj_k', sa.Float(), nullable=True),
    sa.Column('kdj_d', sa.Float(), nullable=True),
    sa.Column('kdj_j', sa.Float(), nullable=True),
    sa.Column('rsi_6', sa.Float(), nullable=True),
    sa.Column('rsi_12', sa.Float(), nullable=True),
    sa.Column('rsi_24', sa.Float(), nullable=True),
    sa.Column('cci', sa.Float(), nullable=True),
    sa.Column('wr_14', sa.Float(), nullable=True),
    sa.Column('boll_upper', sa.Float(), nullable=True),
    sa.Column('boll_mid', sa.Float(), nullable=True),
    sa.Column('boll_lower', sa.Float(), nullable=True),
    sa.Column('atr', sa.Float(), nullable=True),
    sa.Column('obv', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'trade_date', name='idx_stock_indicators_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_indicators_stock_code'), 'stock_indicators', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_indicators_trade_date'), 'stock_indicators', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_insider_trading',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('insider_name', sa.String(length=100), nullable=True),
    sa.Column('relationship', sa.String(length=100), nullable=True),
    sa.Column('change_type', sa.String(length=20), nullable=True),
    sa.Column('change_shares', sa.BigInteger(), nullable=True),
    sa.Column('change_ratio', sa.Float(), nullable=True),
    sa.Column('change_avg_price', sa.Float(), nullable=True),
    sa.Column('trade_date', sa.Date(), nullable=True),
    sa.Column('ann_date', sa.Date(), nullable=True),
    sa.Column('shares_after_change', sa.BigInteger(), nullable=True),
    sa.Column('ratio_after_change', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'insider_name', 'trade_date', 'ann_date', name='idx_insider_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_insider_trading_ann_date'), 'stock_insider_trading', ['ann_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_insider_trading_stock_code'), 'stock_insider_trading', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_insider_trading_trade_date'), 'stock_insider_trading', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_limit_down_pool',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('stock_name', sa.String(length=100), nullable=True),
    sa.Column('update_date', sa.Date(), nullable=False),
    sa.Column('limit_down_price', sa.Float(), nullable=True),
    sa.Column('pct_chg', sa.Float(), nullable=True),
    sa.Column('turnover', sa.Float(), nullable=True),
    sa.Column('circ_mv', sa.Float(), nullable=True),
    sa.Column('total_mv', sa.Float(), nullable=True),
    sa.Column('first_limit_down_time', sa.String(length=20), nullable=True),
    sa.Column('last_limit_down_time', sa.String(length=20), nullable=True),
    sa.Column('limit_down_type', sa.String(length=100), nullable=True),
    sa.Column('limit_down_days', sa.String(length=50), nullable=True),
    sa.Column('limit_down_stats', sa.String(length=50), nullable=True),
    sa.Column('limit_down_reason', sa.Text(), nullable=True),
    sa.Column('turnover_rate', sa.Float(), nullable=True),
    sa.Column('fund_amount', sa.Float(), nullable=True),
    sa.Column('open_times', sa.Integer(), nullable=True),
    sa.Column('board_turnover', sa.Float(), nullable=True),
    sa.Column('dynamic_pe', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'update_date', name='idx_limit_down_stock_date_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_limit_down_pool_stock_code'), 'stock_limit_down_pool', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_limit_down_pool_update_date'), 'stock_limit_down_pool', ['update_date'], unique=False, schema='data')
    op.create_table('stock_limit_up_pool',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('stock_name', sa.String(length=100), nullable=True),
    sa.Column('update_date', sa.Date(), nullable=False),
    sa.Column('limit_up_price', sa.Float(), nullable=True),
    sa.Column('pct_chg', sa.Float(), nullable=True),
    sa.Column('turnover', sa.Float(), nullable=True),
    sa.Column('circ_mv', sa.Float(), nullable=True),
    sa.Column('total_mv', sa.Float(), nullable=True),
    sa.Column('first_limit_up_time', sa.String(length=20), nullable=True),
    sa.Column('last_limit_up_time', sa.String(length=20), nullable=True),
    sa.Column('limit_up_type', sa.String(length=100), nullable=True),
    sa.Column('limit_up_days', sa.String(length=50), nullable=True),
    sa.Column('limit_up_stats', sa.String(length=50), nullable=True),
    sa.Column('limit_up_reason', sa.Text(), nullable=True),
    sa.Column('turnover_rate', sa.Float(), nullable=True),
    sa.Column('fund_amount', sa.Float(), nullable=True),
    sa.Column('open_times', sa.Integer(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'update_date', name='idx_limit_up_stock_date_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_limit_up_pool_stock_code'), 'stock_limit_up_pool', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_limit_up_pool_update_date'), 'stock_limit_up_pool', ['update_date'], unique=False, schema='data')
    op.create_table('stock_lockup_release',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('release_date', sa.Date(), nullable=False),
    sa.Column('release_shares', sa.BigInteger(), nullable=True),
    sa.Column('release_market_value', sa.Float(), nullable=True),
    sa.Column('ratio_to_total', sa.Float(), nullable=True),
    sa.Column('ratio_to_float', sa.Float(), nullable=True),
    sa.Column('release_type', sa.String(length=100), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'release_date', name='idx_release_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_lockup_release_release_date'), 'stock_lockup_release', ['release_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_lockup_release_stock_code'), 'stock_lockup_release', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_margin_data',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('margin_balance', sa.Float(), nullable=True),
    sa.Column('margin_buy_amount', sa.Float(), nullable=True),
    sa.Column('margin_repay_amount', sa.Float(), nullable=True),
    sa.Column('short_balance', sa.Float(), nullable=True),
    sa.Column('short_volume', sa.Float(), nullable=True),
    sa.Column('short_sell_volume', sa.Float(), nullable=True),
    sa.Column('short_repay_volume', sa.Float(), nullable=True),
    sa.Column('margin_short_balance', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'trade_date', name='idx_margin_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_margin_data_stock_code'), 'stock_margin_data', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_margin_data_trade_date'), 'stock_margin_data', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_money_flow',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('net_inflow_small', sa.Float(), nullable=True),
    sa.Column('net_inflow_medium', sa.Float(), nullable=True),
    sa.Column('net_inflow_large', sa.Float(), nullable=True),
    sa.Column('net_inflow_huge', sa.Float(), nullable=True),
    sa.Column('net_inflow_main', sa.Float(), nullable=True),
    sa.Column('net_inflow_ratio_main', sa.Float(), nullable=True),
    sa.Column('close_price', sa.Float(), nullable=True),
    sa.Column('change_pct', sa.Float(), nullable=True),
    sa.Column('net_inflow_ratio_huge', sa.Float(), nullable=True),
    sa.Column('net_inflow_ratio_large', sa.Float(), nullable=True),
    sa.Column('net_inflow_ratio_medium', sa.Float(), nullable=True),
    sa.Column('net_inflow_ratio_small', sa.Float(), nullable=True),
    sa.Column('net_inflow_main_3d', sa.Float(), nullable=True),
    sa.Column('net_inflow_main_5d', sa.Float(), nullable=True),
    sa.Column('net_inflow_main_10d', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'trade_date', name='idx_money_flow_stock_date_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_money_flow_stock_code'), 'stock_money_flow', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_money_flow_trade_date'), 'stock_money_flow', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_pledge_risk',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('pledgor_name', sa.String(length=200), nullable=True),
    sa.Column('pledgee_name', sa.String(length=200), nullable=True),
    sa.Column('pledge_shares', sa.BigInteger(), nullable=True),
    sa.Column('pledge_ratio_to_total', sa.Float(), nullable=True),
    sa.Column('pledge_ratio_to_holder', sa.Float(), nullable=True),
    sa.Column('pledge_date', sa.Date(), nullable=True),
    sa.Column('ann_date', sa.Date(), nullable=True),
    sa.Column('release_date', sa.Date(), nullable=True),
    sa.Column('pledge_price', sa.Float(), nullable=True),
    sa.Column('current_price', sa.Float(), nullable=True),
    sa.Column('liquidate_price', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'pledgor_name', 'pledge_date', name='idx_pledge_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_pledge_risk_stock_code'), 'stock_pledge_risk', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_pledge_summary',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('industry', sa.String(length=100), nullable=True),
    sa.Column('industry_code', sa.String(length=50), nullable=True),
    sa.Column('pledge_ratio', sa.Float(), nullable=True),
    sa.Column('pledge_shares', sa.Float(), nullable=True),
    sa.Column('pledge_market_value', sa.Float(), nullable=True),
    sa.Column('pledge_count', sa.Integer(), nullable=True),
    sa.Column('unrestricted_pledge_shares', sa.Float(), nullable=True),
    sa.Column('restricted_pledge_shares', sa.Float(), nullable=True),
    sa.Column('total_share', sa.Float(), nullable=True),
    sa.Column('price_change_1y', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'trade_date', name='idx_pledge_summary_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_pledge_summary_stock_code'), 'stock_pledge_summary', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_pledge_summary_trade_date'), 'stock_pledge_summary', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_realtime_market',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('current_price', sa.Float(), nullable=True),
    sa.Column('change_percent', sa.Float(), nullable=True),
    sa.Column('change_amount', sa.Float(), nullable=True),
    sa.Column('volume', sa.Float(), nullable=True),
    sa.Column('turnover', sa.Float(), nullable=True),
    sa.Column('amplitude', sa.Float(), nullable=True),
    sa.Column('high', sa.Float(), nullable=True),
    sa.Column('low', sa.Float(), nullable=True),
    sa.Column('open', sa.Float(), nullable=True),
    sa.Column('prev_close', sa.Float(), nullable=True),
    sa.Column('volume_ratio', sa.Float(), nullable=True),
    sa.Column('turnover_rate', sa.Float(), nullable=True),
    sa.Column('pe_dynamic', sa.Float(), nullable=True),
    sa.Column('pb_ratio', sa.Float(), nullable=True),
    sa.Column('total_market_cap', sa.Float(), nullable=True),
    sa.Column('circulating_market_cap', sa.Float(), nullable=True),
    sa.Column('speed_increase', sa.Float(), nullable=True),
    sa.Column('change_5min', sa.Float(), nullable=True),
    sa.Column('change_60days', sa.Float(), nullable=True),
    sa.Column('change_ytd', sa.Float(), nullable=True),
    sa.Column('main_net_inflow_today', sa.Float(), nullable=True),
    sa.Column('super_big_inflow_today', sa.Float(), nullable=True),
    sa.Column('big_inflow_today', sa.Float(), nullable=True),
    sa.Column('mid_inflow_today', sa.Float(), nullable=True),
    sa.Column('small_inflow_today', sa.Float(), nullable=True),
    sa.Column('main_net_inflow_rank_today', sa.Integer(), nullable=True),
    sa.Column('main_net_inflow_5d', sa.Float(), nullable=True),
    sa.Column('super_big_inflow_5d', sa.Float(), nullable=True),
    sa.Column('big_inflow_5d', sa.Float(), nullable=True),
    sa.Column('mid_inflow_5d', sa.Float(), nullable=True),
    sa.Column('small_inflow_5d', sa.Float(), nullable=True),
    sa.Column('main_net_inflow_rank_5d', sa.Integer(), nullable=True),
    sa.Column('main_net_inflow_10d', sa.Float(), nullable=True),
    sa.Column('super_big_inflow_10d', sa.Float(), nullable=True),
    sa.Column('big_inflow_10d', sa.Float(), nullable=True),
    sa.Column('mid_inflow_10d', sa.Float(), nullable=True),
    sa.Column('small_inflow_10d', sa.Float(), nullable=True),
    sa.Column('main_net_inflow_rank_10d', sa.Integer(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_realtime_market_stock_code'), 'stock_realtime_market', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_sentiment',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('sentiment_score', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('article_count', sa.Integer(), nullable=True),
    sa.Column('positive_count', sa.Integer(), nullable=True),
    sa.Column('negative_count', sa.Integer(), nullable=True),
    sa.Column('neutral_count', sa.Integer(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'trade_date', name='idx_sentiment_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_sentiment_stock_code'), 'stock_sentiment', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_sentiment_trade_date'), 'stock_sentiment', ['trade_date'], unique=False, schema='data')
    op.create_table('stock_seo',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('issue_date', sa.Date(), nullable=True),
    sa.Column('announce_date', sa.Date(), nullable=True),
    sa.Column('issue_price', sa.Float(), nullable=True),
    sa.Column('issue_volume', sa.Float(), nullable=True),
    sa.Column('raise_amount', sa.Float(), nullable=True),
    sa.Column('issue_object', sa.Text(), nullable=True),
    sa.Column('lock_period', sa.String(length=50), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_seo_issue_date'), 'stock_seo', ['issue_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_seo_stock_code'), 'stock_seo', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_shareholder_count',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('ann_date', sa.Date(), nullable=True),
    sa.Column('holder_count', sa.BigInteger(), nullable=True),
    sa.Column('holder_count_prev', sa.BigInteger(), nullable=True),
    sa.Column('holder_count_change', sa.Float(), nullable=True),
    sa.Column('holder_count_change_ratio', sa.Float(), nullable=True),
    sa.Column('avg_hold_shares', sa.Float(), nullable=True),
    sa.Column('avg_hold_shares_prev', sa.Float(), nullable=True),
    sa.Column('avg_hold_shares_change_ratio', sa.Float(), nullable=True),
    sa.Column('avg_hold_value', sa.Float(), nullable=True),
    sa.Column('total_mv', sa.Float(), nullable=True),
    sa.Column('total_share', sa.Float(), nullable=True),
    sa.Column('share_change', sa.Float(), nullable=True),
    sa.Column('share_change_reason', sa.String(length=255), nullable=True),
    sa.Column('price_at_end', sa.Float(), nullable=True),
    sa.Column('price_change_ratio', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'end_date', name='idx_shareholder_stock_date_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_shareholder_count_ann_date'), 'stock_shareholder_count', ['ann_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_shareholder_count_end_date'), 'stock_shareholder_count', ['end_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_shareholder_count_stock_code'), 'stock_shareholder_count', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_top_holders',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('report_date', sa.Date(), nullable=False),
    sa.Column('holder_name', sa.String(length=200), nullable=True),
    sa.Column('holder_type', sa.String(length=50), nullable=True),
    sa.Column('hold_amount', sa.Float(), nullable=True),
    sa.Column('hold_ratio', sa.Float(), nullable=True),
    sa.Column('change', sa.String(length=50), nullable=True),
    sa.Column('change_ratio', sa.Float(), nullable=True),
    sa.Column('holder_rank', sa.Integer(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'report_date', 'holder_name', name='idx_top_holder_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_top_holders_report_date'), 'stock_top_holders', ['report_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_top_holders_stock_code'), 'stock_top_holders', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_valuation_history',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('data_date', sa.Date(), nullable=False),
    sa.Column('close_price', sa.Float(), nullable=True),
    sa.Column('change_percent', sa.Float(), nullable=True),
    sa.Column('total_market_value', sa.BigInteger(), nullable=True),
    sa.Column('circulating_market_value', sa.BigInteger(), nullable=True),
    sa.Column('total_share', sa.Float(), nullable=True),
    sa.Column('float_share', sa.Float(), nullable=True),
    sa.Column('free_share', sa.Float(), nullable=True),
    sa.Column('pe_ttm', sa.Float(), nullable=True),
    sa.Column('pe_static', sa.Float(), nullable=True),
    sa.Column('pb', sa.Float(), nullable=True),
    sa.Column('ps_ttm', sa.Float(), nullable=True),
    sa.Column('ps_static', sa.Float(), nullable=True),
    sa.Column('peg', sa.Float(), nullable=True),
    sa.Column('dividend_yield', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'data_date', name='idx_valuation_stock_date_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_valuation_history_data_date'), 'stock_valuation_history', ['data_date'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_valuation_history_stock_code'), 'stock_valuation_history', ['stock_code'], unique=False, schema='data')
    op.create_table('stock_zhaban_pool',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('stock_name', sa.String(length=100), nullable=True),
    sa.Column('update_date', sa.Date(), nullable=False),
    sa.Column('latest_price', sa.Float(), nullable=True),
    sa.Column('limit_up_price', sa.Float(), nullable=True),
    sa.Column('pct_chg', sa.Float(), nullable=True),
    sa.Column('turnover', sa.Float(), nullable=True),
    sa.Column('circ_mv', sa.Float(), nullable=True),
    sa.Column('total_mv', sa.Float(), nullable=True),
    sa.Column('first_limit_up_time', sa.String(length=20), nullable=True),
    sa.Column('last_limit_up_time', sa.String(length=20), nullable=True),
    sa.Column('limit_up_type', sa.String(length=100), nullable=True),
    sa.Column('turnover_rate', sa.Float(), nullable=True),
    sa.Column('swing', sa.Float(), nullable=True),
    sa.Column('open_times', sa.Integer(), nullable=True),
    sa.Column('limit_up_stats', sa.String(length=50), nullable=True),
    sa.Column('limit_up_reason', sa.Text(), nullable=True),
    sa.Column('speed_increase', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'update_date', name='idx_zhaban_stock_date_unique'),
    schema='data'
    )
    op.create_index(op.f('ix_data_stock_zhaban_pool_stock_code'), 'stock_zhaban_pool', ['stock_code'], unique=False, schema='data')
    op.create_index(op.f('ix_data_stock_zhaban_pool_update_date'), 'stock_zhaban_pool', ['update_date'], unique=False, schema='data')
    op.create_table('market_watch_events',
    sa.Column('event_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.String(length=100), nullable=True),
    sa.Column('watch_ai_decision', sa.JSON(), nullable=True),
    sa.Column('debate_parameters', sa.JSON(), nullable=True),
    sa.Column('debate_session_id', sa.String(length=36), nullable=True),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('event_id')
    )
    op.create_index(op.f('ix_market_watch_events_created_at'), 'market_watch_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_market_watch_events_event_type'), 'market_watch_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_market_watch_events_status'), 'market_watch_events', ['status'], unique=False)
    op.create_index(op.f('ix_market_watch_events_user_id'), 'market_watch_events', ['user_id'], unique=False)
    op.create_table('sessions',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('stock_code', sa.String(length=10), nullable=True),
    sa.Column('trading_frequency', sa.String(length=50), nullable=False),
    sa.Column('trading_strategy', sa.String(length=50), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.Enum('active', 'completed', 'failed', 'archived', name='session_status'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('session_id')
    )
    op.create_index(op.f('ix_sessions_stock_code'), 'sessions', ['stock_code'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('research_runs',
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('current_stage', sa.String(length=50), nullable=False),
    sa.Column('current_phase', sa.String(length=30), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('raw_requirement', sa.Text(), nullable=False),
    sa.Column('pending_message_id', sa.UUID(), nullable=True),
    sa.Column('checkpoint_payload', sa.JSON(), nullable=False),
    sa.Column('cache_context_version', sa.String(length=80), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("current_phase IN ('planning', 'reflection', 'research', 'synthesis')", name='ck_interactive_research_runs_phase'),
    sa.CheckConstraint("status IN ('awaiting_plan_approval', 'awaiting_user_input', 'cancelled', 'completed', 'drafting_plan', 'failed', 'reflecting', 'researching', 'synthesizing')", name='ck_interactive_research_runs_status'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('run_id'),
    schema='stock_picker_interactive'
    )
    op.create_index('ix_interactive_research_runs_status_created_at', 'research_runs', ['status', 'created_at'], unique=False, schema='stock_picker_interactive')
    op.create_index('ix_interactive_research_runs_user_created_at', 'research_runs', ['user_id', 'created_at'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_current_phase'), 'research_runs', ['current_phase'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_pending_message_id'), 'research_runs', ['pending_message_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_status'), 'research_runs', ['status'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_runs_user_id'), 'research_runs', ['user_id'], unique=False, schema='stock_picker_interactive')
    op.create_table('stock_warehouse',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=True),
    sa.Column('auto_analysis_enabled', sa.Boolean(), nullable=False),
    sa.Column('auto_analysis_frequency', sa.String(length=20), nullable=False),
    sa.Column('auto_analysis_time', sa.String(length=5), nullable=False),
    sa.Column('auto_analysis_trading_frequency', sa.String(length=50), nullable=False),
    sa.Column('auto_analysis_trading_strategy', sa.String(length=50), nullable=False),
    sa.Column('auto_analysis_run_immediately', sa.Boolean(), nullable=False),
    sa.Column('last_auto_analysis_at', sa.DateTime(), nullable=True),
    sa.Column('last_auto_analysis_session_id', sa.String(length=36), nullable=True),
    sa.Column('last_auto_analysis_task_id', sa.String(length=36), nullable=True),
    sa.Column('last_auto_analysis_error', sa.Text(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['stock_code'], ['data.stock_basic.stock_code'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_code', 'user_id', name='_stock_user_uc')
    )
    op.create_index(op.f('ix_stock_warehouse_id'), 'stock_warehouse', ['id'], unique=False)
    op.create_index(op.f('ix_stock_warehouse_stock_code'), 'stock_warehouse', ['stock_code'], unique=False)
    op.create_index(op.f('ix_stock_warehouse_user_id'), 'stock_warehouse', ['user_id'], unique=False)
    op.create_table('system_settings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_system_settings_global_key_unique', 'system_settings', ['key'], unique=True, postgresql_where=sa.text('user_id IS NULL'), sqlite_where=sa.text('user_id IS NULL'))
    op.create_index(op.f('ix_system_settings_key'), 'system_settings', ['key'], unique=False)
    op.create_index(op.f('ix_system_settings_user_id'), 'system_settings', ['user_id'], unique=False)
    op.create_index('ix_system_settings_user_key_unique', 'system_settings', ['user_id', 'key'], unique=True, postgresql_where=sa.text('user_id IS NOT NULL'), sqlite_where=sa.text('user_id IS NOT NULL'))
    op.create_table('account_equity_snapshots',
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_date', sa.Date(), nullable=False),
    sa.Column('total_assets', sa.DECIMAL(precision=18, scale=4), nullable=False),
    sa.Column('available_cash', sa.DECIMAL(precision=18, scale=4), nullable=False),
    sa.Column('market_value', sa.DECIMAL(precision=18, scale=4), nullable=False),
    sa.Column('position_count', sa.Integer(), nullable=False),
    sa.Column('daily_return', sa.DECIMAL(precision=18, scale=8), nullable=True),
    sa.Column('cumulative_return', sa.DECIMAL(precision=18, scale=8), nullable=True),
    sa.Column('benchmark_code', sa.String(length=20), nullable=False),
    sa.Column('benchmark_close', sa.DECIMAL(precision=18, scale=6), nullable=True),
    sa.Column('benchmark_daily_return', sa.DECIMAL(precision=18, scale=8), nullable=True),
    sa.Column('benchmark_cumulative_return', sa.DECIMAL(precision=18, scale=8), nullable=True),
    sa.Column('excess_return', sa.DECIMAL(precision=18, scale=8), nullable=True),
    sa.Column('max_drawdown', sa.DECIMAL(precision=18, scale=8), nullable=True),
    sa.Column('missing_benchmark_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('snapshot_id'),
    sa.UniqueConstraint('account_id', 'snapshot_date', 'benchmark_code', name='idx_account_equity_snapshot_unique')
    )
    op.create_index(op.f('ix_account_equity_snapshots_account_id'), 'account_equity_snapshots', ['account_id'], unique=False)
    op.create_index(op.f('ix_account_equity_snapshots_snapshot_date'), 'account_equity_snapshots', ['snapshot_date'], unique=False)
    op.create_index(op.f('ix_account_equity_snapshots_user_id'), 'account_equity_snapshots', ['user_id'], unique=False)
    op.create_table('debate_messages',
    sa.Column('message_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('stage', sa.String(length=50), nullable=False),
    sa.Column('round_number', sa.Integer(), nullable=False),
    sa.Column('agent_name', sa.String(length=100), nullable=False),
    sa.Column('agent_role', sa.String(length=50), nullable=False),
    sa.Column('decision', sa.Text(), nullable=True),
    sa.Column('reasoning', sa.Text(), nullable=True),
    sa.Column('prompt_input', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('message_id')
    )
    op.create_index(op.f('ix_debate_messages_session_id'), 'debate_messages', ['session_id'], unique=False)
    op.create_index(op.f('ix_debate_messages_stage'), 'debate_messages', ['stage'], unique=False)
    op.create_table('experience_indexes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('memory_id', sa.String(length=100), nullable=False),
    sa.Column('review_run_id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('stock_code', sa.String(length=20), nullable=True),
    sa.Column('stock_name', sa.String(length=100), nullable=True),
    sa.Column('industry', sa.String(length=100), nullable=True),
    sa.Column('strategy', sa.String(length=100), nullable=True),
    sa.Column('review_horizon', sa.String(length=10), nullable=True),
    sa.Column('outcome_label', sa.String(length=50), nullable=True),
    sa.Column('correctness', sa.String(length=50), nullable=True),
    sa.Column('importance', sa.String(length=20), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'memory_id', name='uq_experience_indexes_user_memory_id')
    )
    op.create_index(op.f('ix_experience_indexes_correctness'), 'experience_indexes', ['correctness'], unique=False)
    op.create_index(op.f('ix_experience_indexes_created_at'), 'experience_indexes', ['created_at'], unique=False)
    op.create_index(op.f('ix_experience_indexes_importance'), 'experience_indexes', ['importance'], unique=False)
    op.create_index(op.f('ix_experience_indexes_industry'), 'experience_indexes', ['industry'], unique=False)
    op.create_index(op.f('ix_experience_indexes_memory_id'), 'experience_indexes', ['memory_id'], unique=False)
    op.create_index(op.f('ix_experience_indexes_outcome_label'), 'experience_indexes', ['outcome_label'], unique=False)
    op.create_index(op.f('ix_experience_indexes_review_horizon'), 'experience_indexes', ['review_horizon'], unique=False)
    op.create_index(op.f('ix_experience_indexes_review_run_id'), 'experience_indexes', ['review_run_id'], unique=False)
    op.create_index(op.f('ix_experience_indexes_session_id'), 'experience_indexes', ['session_id'], unique=False)
    op.create_index(op.f('ix_experience_indexes_stock_code'), 'experience_indexes', ['stock_code'], unique=False)
    op.create_index(op.f('ix_experience_indexes_strategy'), 'experience_indexes', ['strategy'], unique=False)
    op.create_index(op.f('ix_experience_indexes_user_id'), 'experience_indexes', ['user_id'], unique=False)
    op.create_table('experience_review_events',
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('review_run_id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('stage', sa.String(length=50), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('message_key', sa.String(length=255), nullable=True),
    sa.Column('message_params', sa.JSON(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('event_id')
    )
    op.create_index(op.f('ix_experience_review_events_created_at'), 'experience_review_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_experience_review_events_review_run_id'), 'experience_review_events', ['review_run_id'], unique=False)
    op.create_index(op.f('ix_experience_review_events_session_id'), 'experience_review_events', ['session_id'], unique=False)
    op.create_index(op.f('ix_experience_review_events_stage'), 'experience_review_events', ['stage'], unique=False)
    op.create_index(op.f('ix_experience_review_events_status'), 'experience_review_events', ['status'], unique=False)
    op.create_index(op.f('ix_experience_review_events_user_id'), 'experience_review_events', ['user_id'], unique=False)
    op.create_table('orders',
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('account_id', sa.UUID(), nullable=True),
    sa.Column('stock_code', sa.String(length=10), nullable=True),
    sa.Column('action', sa.String(length=10), nullable=True),
    sa.Column('order_type', sa.String(length=10), nullable=True),
    sa.Column('price', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('shares', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'filled', 'cancelled', 'rejected', name='order_status'), nullable=True),
    sa.Column('filled_shares', sa.BigInteger(), nullable=True),
    sa.Column('avg_fill_price', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('realized_pnl', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('filled_at', sa.DateTime(), nullable=True),
    sa.Column('remark', sa.String(length=500), nullable=True),
    sa.Column('source', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], ),
    sa.PrimaryKeyConstraint('order_id')
    )
    op.create_index('idx_order_created', 'orders', ['created_at'], unique=False)
    op.create_index('idx_order_session', 'orders', ['session_id'], unique=False)
    op.create_index('idx_order_status', 'orders', ['status'], unique=False)
    op.create_index(op.f('ix_orders_action'), 'orders', ['action'], unique=False)
    op.create_index(op.f('ix_orders_stock_code'), 'orders', ['stock_code'], unique=False)
    op.create_table('pm_decisions',
    sa.Column('decision_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('stock_code', sa.String(length=10), nullable=False),
    sa.Column('target_position', sa.Float(), nullable=False),
    sa.Column('confidence_score', sa.Float(), nullable=False),
    sa.Column('stop_loss', sa.Float(), nullable=True),
    sa.Column('take_profit', sa.Float(), nullable=True),
    sa.Column('holding_horizon_days', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('decision_id'),
    sa.UniqueConstraint('session_id', name='uq_pm_decisions_session_id')
    )
    op.create_index(op.f('ix_pm_decisions_session_id'), 'pm_decisions', ['session_id'], unique=False)
    op.create_index(op.f('ix_pm_decisions_stock_code'), 'pm_decisions', ['stock_code'], unique=False)
    op.create_index(op.f('ix_pm_decisions_user_id'), 'pm_decisions', ['user_id'], unique=False)
    op.create_table('positions',
    sa.Column('position_id', sa.UUID(), nullable=False),
    sa.Column('account_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('stock_code', sa.String(length=10), nullable=True),
    sa.Column('total_shares', sa.Integer(), nullable=True),
    sa.Column('available_shares', sa.Integer(), nullable=True),
    sa.Column('frozen_shares', sa.Integer(), nullable=True),
    sa.Column('avg_cost', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('current_price', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('market_value', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('profit_loss', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('profit_loss_pct', sa.DECIMAL(precision=5, scale=4), nullable=True),
    sa.Column('purchase_details', sa.JSON(), nullable=True),
    sa.Column('stop_loss', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('take_profit', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('horizon_deadline', sa.DateTime(), nullable=True),
    sa.Column('pm_session_id', sa.UUID(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], ),
    sa.PrimaryKeyConstraint('position_id'),
    sa.UniqueConstraint('account_id', 'stock_code', name='uq_positions_account_stock_code')
    )
    op.create_index(op.f('ix_positions_stock_code'), 'positions', ['stock_code'], unique=False)
    op.create_table('research_messages',
    sa.Column('message_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('message_type', sa.String(length=40), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('parent_message_id', sa.UUID(), nullable=True),
    sa.Column('sequence_no', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('visible_to_user', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("message_type IN ('assistant_question', 'assistant_text', 'final_result', 'plan_card', 'progress_update', 'system_status', 'tool_result', 'tool_start', 'user_input')", name='ck_interactive_messages_type'),
    sa.CheckConstraint("role IN ('assistant', 'system', 'tool', 'user')", name='ck_interactive_messages_role'),
    sa.CheckConstraint("status IN ('completed', 'created', 'failed', 'queued', 'streaming')", name='ck_interactive_messages_status'),
    sa.ForeignKeyConstraint(['run_id'], ['stock_picker_interactive.research_runs.run_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('message_id'),
    sa.UniqueConstraint('run_id', 'sequence_no', name='uq_interactive_messages_run_sequence'),
    schema='stock_picker_interactive'
    )
    op.create_index('ix_interactive_messages_run_created_at', 'research_messages', ['run_id', 'created_at'], unique=False, schema='stock_picker_interactive')
    op.create_index('ix_interactive_messages_run_sequence', 'research_messages', ['run_id', 'sequence_no'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_messages_message_type'), 'research_messages', ['message_type'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_messages_parent_message_id'), 'research_messages', ['parent_message_id'], unique=False, schema='stock_picker_interactive')
    op.create_index(op.f('ix_stock_picker_interactive_research_messages_run_id'), 'research_messages', ['run_id'], unique=False, schema='stock_picker_interactive')
    op.create_table('trade_records',
    sa.Column('trade_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('account_id', sa.UUID(), nullable=True),
    sa.Column('order_id', sa.UUID(), nullable=True),
    sa.Column('stock_code', sa.String(length=10), nullable=True),
    sa.Column('action', sa.String(length=10), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('fill_price', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('commission', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('stamp_duty', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('transfer_fee', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('total_fees', sa.DECIMAL(precision=10, scale=4), nullable=True),
    sa.Column('net_amount', sa.DECIMAL(precision=15, scale=4), nullable=True),
    sa.Column('trade_time', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], ),
    sa.PrimaryKeyConstraint('trade_id')
    )
    op.create_index('idx_session', 'trade_records', ['session_id'], unique=False)
    op.create_index('idx_stock', 'trade_records', ['stock_code'], unique=False)
    op.create_index('idx_time', 'trade_records', ['trade_time'], unique=False)
    op.create_index(op.f('ix_trade_records_action'), 'trade_records', ['action'], unique=False)
    op.create_index(op.f('ix_trade_records_stock_code'), 'trade_records', ['stock_code'], unique=False)
    # 版本表由 Alembic 管理，不在本 revision 中创建或删除。


def downgrade() -> None:
    """回滚数据库结构变更。"""
    # 先删除表和索引，再删除本 revision 创建的业务 schema。
    op.drop_index(op.f('ix_trade_records_stock_code'), table_name='trade_records')
    op.drop_index(op.f('ix_trade_records_action'), table_name='trade_records')
    op.drop_index('idx_time', table_name='trade_records')
    op.drop_index('idx_stock', table_name='trade_records')
    op.drop_index('idx_session', table_name='trade_records')
    op.drop_table('trade_records')
    op.drop_index(op.f('ix_stock_picker_interactive_research_messages_run_id'), table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_messages_parent_message_id'), table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_messages_message_type'), table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index('ix_interactive_messages_run_sequence', table_name='research_messages', schema='stock_picker_interactive')
    op.drop_index('ix_interactive_messages_run_created_at', table_name='research_messages', schema='stock_picker_interactive')
    op.drop_table('research_messages', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_positions_stock_code'), table_name='positions')
    op.drop_table('positions')
    op.drop_index(op.f('ix_pm_decisions_user_id'), table_name='pm_decisions')
    op.drop_index(op.f('ix_pm_decisions_stock_code'), table_name='pm_decisions')
    op.drop_index(op.f('ix_pm_decisions_session_id'), table_name='pm_decisions')
    op.drop_table('pm_decisions')
    op.drop_index(op.f('ix_orders_stock_code'), table_name='orders')
    op.drop_index(op.f('ix_orders_action'), table_name='orders')
    op.drop_index('idx_order_status', table_name='orders')
    op.drop_index('idx_order_session', table_name='orders')
    op.drop_index('idx_order_created', table_name='orders')
    op.drop_table('orders')
    op.drop_index(op.f('ix_experience_review_events_user_id'), table_name='experience_review_events')
    op.drop_index(op.f('ix_experience_review_events_status'), table_name='experience_review_events')
    op.drop_index(op.f('ix_experience_review_events_stage'), table_name='experience_review_events')
    op.drop_index(op.f('ix_experience_review_events_session_id'), table_name='experience_review_events')
    op.drop_index(op.f('ix_experience_review_events_review_run_id'), table_name='experience_review_events')
    op.drop_index(op.f('ix_experience_review_events_created_at'), table_name='experience_review_events')
    op.drop_table('experience_review_events')
    op.drop_index(op.f('ix_experience_indexes_user_id'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_strategy'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_stock_code'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_session_id'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_review_run_id'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_review_horizon'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_outcome_label'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_memory_id'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_industry'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_importance'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_created_at'), table_name='experience_indexes')
    op.drop_index(op.f('ix_experience_indexes_correctness'), table_name='experience_indexes')
    op.drop_table('experience_indexes')
    op.drop_index(op.f('ix_debate_messages_stage'), table_name='debate_messages')
    op.drop_index(op.f('ix_debate_messages_session_id'), table_name='debate_messages')
    op.drop_table('debate_messages')
    op.drop_index(op.f('ix_account_equity_snapshots_user_id'), table_name='account_equity_snapshots')
    op.drop_index(op.f('ix_account_equity_snapshots_snapshot_date'), table_name='account_equity_snapshots')
    op.drop_index(op.f('ix_account_equity_snapshots_account_id'), table_name='account_equity_snapshots')
    op.drop_table('account_equity_snapshots')
    op.drop_index('ix_system_settings_user_key_unique', table_name='system_settings', postgresql_where=sa.text('user_id IS NOT NULL'), sqlite_where=sa.text('user_id IS NOT NULL'))
    op.drop_index(op.f('ix_system_settings_user_id'), table_name='system_settings')
    op.drop_index(op.f('ix_system_settings_key'), table_name='system_settings')
    op.drop_index('ix_system_settings_global_key_unique', table_name='system_settings', postgresql_where=sa.text('user_id IS NULL'), sqlite_where=sa.text('user_id IS NULL'))
    op.drop_table('system_settings')
    op.drop_index(op.f('ix_stock_warehouse_user_id'), table_name='stock_warehouse')
    op.drop_index(op.f('ix_stock_warehouse_stock_code'), table_name='stock_warehouse')
    op.drop_index(op.f('ix_stock_warehouse_id'), table_name='stock_warehouse')
    op.drop_table('stock_warehouse')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_user_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_status'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_pending_message_id'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_stock_picker_interactive_research_runs_current_phase'), table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index('ix_interactive_research_runs_user_created_at', table_name='research_runs', schema='stock_picker_interactive')
    op.drop_index('ix_interactive_research_runs_status_created_at', table_name='research_runs', schema='stock_picker_interactive')
    op.drop_table('research_runs', schema='stock_picker_interactive')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_stock_code'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_market_watch_events_user_id'), table_name='market_watch_events')
    op.drop_index(op.f('ix_market_watch_events_status'), table_name='market_watch_events')
    op.drop_index(op.f('ix_market_watch_events_event_type'), table_name='market_watch_events')
    op.drop_index(op.f('ix_market_watch_events_created_at'), table_name='market_watch_events')
    op.drop_table('market_watch_events')
    op.drop_index(op.f('ix_data_stock_zhaban_pool_update_date'), table_name='stock_zhaban_pool', schema='data')
    op.drop_index(op.f('ix_data_stock_zhaban_pool_stock_code'), table_name='stock_zhaban_pool', schema='data')
    op.drop_table('stock_zhaban_pool', schema='data')
    op.drop_index(op.f('ix_data_stock_valuation_history_stock_code'), table_name='stock_valuation_history', schema='data')
    op.drop_index(op.f('ix_data_stock_valuation_history_data_date'), table_name='stock_valuation_history', schema='data')
    op.drop_table('stock_valuation_history', schema='data')
    op.drop_index(op.f('ix_data_stock_top_holders_stock_code'), table_name='stock_top_holders', schema='data')
    op.drop_index(op.f('ix_data_stock_top_holders_report_date'), table_name='stock_top_holders', schema='data')
    op.drop_table('stock_top_holders', schema='data')
    op.drop_index(op.f('ix_data_stock_shareholder_count_stock_code'), table_name='stock_shareholder_count', schema='data')
    op.drop_index(op.f('ix_data_stock_shareholder_count_end_date'), table_name='stock_shareholder_count', schema='data')
    op.drop_index(op.f('ix_data_stock_shareholder_count_ann_date'), table_name='stock_shareholder_count', schema='data')
    op.drop_table('stock_shareholder_count', schema='data')
    op.drop_index(op.f('ix_data_stock_seo_stock_code'), table_name='stock_seo', schema='data')
    op.drop_index(op.f('ix_data_stock_seo_issue_date'), table_name='stock_seo', schema='data')
    op.drop_table('stock_seo', schema='data')
    op.drop_index(op.f('ix_data_stock_sentiment_trade_date'), table_name='stock_sentiment', schema='data')
    op.drop_index(op.f('ix_data_stock_sentiment_stock_code'), table_name='stock_sentiment', schema='data')
    op.drop_table('stock_sentiment', schema='data')
    op.drop_index(op.f('ix_data_stock_realtime_market_stock_code'), table_name='stock_realtime_market', schema='data')
    op.drop_table('stock_realtime_market', schema='data')
    op.drop_index(op.f('ix_data_stock_pledge_summary_trade_date'), table_name='stock_pledge_summary', schema='data')
    op.drop_index(op.f('ix_data_stock_pledge_summary_stock_code'), table_name='stock_pledge_summary', schema='data')
    op.drop_table('stock_pledge_summary', schema='data')
    op.drop_index(op.f('ix_data_stock_pledge_risk_stock_code'), table_name='stock_pledge_risk', schema='data')
    op.drop_table('stock_pledge_risk', schema='data')
    op.drop_index(op.f('ix_data_stock_money_flow_trade_date'), table_name='stock_money_flow', schema='data')
    op.drop_index(op.f('ix_data_stock_money_flow_stock_code'), table_name='stock_money_flow', schema='data')
    op.drop_table('stock_money_flow', schema='data')
    op.drop_index(op.f('ix_data_stock_margin_data_trade_date'), table_name='stock_margin_data', schema='data')
    op.drop_index(op.f('ix_data_stock_margin_data_stock_code'), table_name='stock_margin_data', schema='data')
    op.drop_table('stock_margin_data', schema='data')
    op.drop_index(op.f('ix_data_stock_lockup_release_stock_code'), table_name='stock_lockup_release', schema='data')
    op.drop_index(op.f('ix_data_stock_lockup_release_release_date'), table_name='stock_lockup_release', schema='data')
    op.drop_table('stock_lockup_release', schema='data')
    op.drop_index(op.f('ix_data_stock_limit_up_pool_update_date'), table_name='stock_limit_up_pool', schema='data')
    op.drop_index(op.f('ix_data_stock_limit_up_pool_stock_code'), table_name='stock_limit_up_pool', schema='data')
    op.drop_table('stock_limit_up_pool', schema='data')
    op.drop_index(op.f('ix_data_stock_limit_down_pool_update_date'), table_name='stock_limit_down_pool', schema='data')
    op.drop_index(op.f('ix_data_stock_limit_down_pool_stock_code'), table_name='stock_limit_down_pool', schema='data')
    op.drop_table('stock_limit_down_pool', schema='data')
    op.drop_index(op.f('ix_data_stock_insider_trading_trade_date'), table_name='stock_insider_trading', schema='data')
    op.drop_index(op.f('ix_data_stock_insider_trading_stock_code'), table_name='stock_insider_trading', schema='data')
    op.drop_index(op.f('ix_data_stock_insider_trading_ann_date'), table_name='stock_insider_trading', schema='data')
    op.drop_table('stock_insider_trading', schema='data')
    op.drop_index(op.f('ix_data_stock_indicators_trade_date'), table_name='stock_indicators', schema='data')
    op.drop_index(op.f('ix_data_stock_indicators_stock_code'), table_name='stock_indicators', schema='data')
    op.drop_table('stock_indicators', schema='data')
    op.drop_index(op.f('ix_data_stock_hot_rank_timestamp'), table_name='stock_hot_rank', schema='data')
    op.drop_index(op.f('ix_data_stock_hot_rank_stock_code'), table_name='stock_hot_rank', schema='data')
    op.drop_index('idx_hot_rank_query', table_name='stock_hot_rank', schema='data')
    op.drop_table('stock_hot_rank', schema='data')
    op.drop_index(op.f('ix_data_stock_fund_holding_stock_code'), table_name='stock_fund_holding', schema='data')
    op.drop_index(op.f('ix_data_stock_fund_holding_report_date'), table_name='stock_fund_holding', schema='data')
    op.drop_table('stock_fund_holding', schema='data')
    op.drop_index(op.f('ix_data_stock_block_trade_trade_date'), table_name='stock_block_trade', schema='data')
    op.drop_index(op.f('ix_data_stock_block_trade_stock_code'), table_name='stock_block_trade', schema='data')
    op.drop_table('stock_block_trade', schema='data')
    op.drop_index(op.f('ix_data_northbound_data_stock_code'), table_name='northbound_data', schema='data')
    op.drop_index(op.f('ix_data_northbound_data_date'), table_name='northbound_data', schema='data')
    op.drop_table('northbound_data', schema='data')
    op.drop_index(op.f('ix_data_kline_data_stock_code'), table_name='kline_data', schema='data')
    op.drop_index(op.f('ix_data_kline_data_date'), table_name='kline_data', schema='data')
    op.drop_table('kline_data', schema='data')
    op.drop_index(op.f('ix_data_financial_calendar_stock_code'), table_name='financial_calendar', schema='data')
    op.drop_index(op.f('ix_data_financial_calendar_actual_date'), table_name='financial_calendar', schema='data')
    op.drop_table('financial_calendar', schema='data')
    op.drop_index(op.f('ix_data_dragon_tiger_data_trade_date'), table_name='dragon_tiger_data', schema='data')
    op.drop_index(op.f('ix_data_dragon_tiger_data_stock_code'), table_name='dragon_tiger_data', schema='data')
    op.drop_table('dragon_tiger_data', schema='data')
    op.drop_index(op.f('ix_async_tasks_user_id'), table_name='async_tasks')
    op.drop_table('async_tasks')
    op.drop_table('accounts')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_llm_usage_logs_workflow'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_stage'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_session_id'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_role'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_model'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_created_at'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_call_kind'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_cache_lane'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_llm_usage_logs_api_key_alias'), table_name='llm_usage_logs')
    op.drop_table('llm_usage_logs')
    op.drop_index(op.f('ix_data_stock_basic_stock_code'), table_name='stock_basic', schema='data')
    op.drop_table('stock_basic', schema='data')
    op.drop_index(op.f('ix_data_sector_money_flow_trade_date'), table_name='sector_money_flow', schema='data')
    op.drop_index(op.f('ix_data_sector_money_flow_sector_name'), table_name='sector_money_flow', schema='data')
    op.drop_table('sector_money_flow', schema='data')
    op.drop_index(op.f('ix_data_industry_data_board_name'), table_name='industry_data', schema='data')
    op.drop_index(op.f('ix_data_industry_data_board_code'), table_name='industry_data', schema='data')
    op.drop_table('industry_data', schema='data')
    op.drop_index(op.f('ix_data_index_daily_trade_date'), table_name='index_daily', schema='data')
    op.drop_index(op.f('ix_data_index_daily_index_code'), table_name='index_daily', schema='data')
    op.drop_table('index_daily', schema='data')
    op.drop_table('common_data', schema='data')
    op.drop_table('api_registry', schema='data')
    op.execute("DROP TYPE IF EXISTS order_status")
    op.execute("DROP TYPE IF EXISTS session_status")
    op.execute("DROP SCHEMA IF EXISTS stock_picker_interactive")
    op.execute("DROP SCHEMA IF EXISTS data")
