import asyncio
import uuid
from functools import wraps
from typing import Dict, Any, Callable, Optional, List
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore

from app.core.logger import get_logger
from app.data.market_utils import is_trading_time
from app.tasks.async_task_runner import async_task_runner
# 导入任务函数 (Import task functions)
from app.tasks.task_functions import (
    sync_all_stock_basic_func, sync_industry_data_func,
    sync_stock_daily_func,
    calculate_indicators_func, sync_limit_up_pool_func, sync_sector_money_flow_func,
    sync_dragon_tiger_data_func, sync_northbound_data_func,
    sync_valuation_data_func, sync_pledge_summary_func,
    sync_top_holders_func,
    sync_realtime_market_func, sync_granular_data_func,
    execute_daily_settlement_func, sync_bulk_tables_func,
    cleanup_stock_realtime_market_history,
)

# 获取日志记录器
logger = get_logger(__name__)


def handle_job_cancellation(func):
    """
    装饰器：处理异步任务抛出的 CancelledError。
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            func_name = getattr(func, '__name__', 'unknown_job')
            logger.info(f"Task {func_name} was cancelled during shutdown")
            return None
    return wrapper


class DataRefreshScheduler:
    """数据刷新调度器 (Data Refresh Scheduler)"""

    def __init__(self):
        self.scheduler = None
        self.init_scheduler()

    def init_scheduler(self):
        """初始化调度器 (Initialize scheduler)"""
        jobstores = {'default': MemoryJobStore()}
        job_defaults = {
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': 3600
        }

        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            job_defaults=job_defaults,
            timezone='Asia/Shanghai'
        )

    def start(self):
        """启动调度器 (Start scheduler)"""
        if self.scheduler and not self.scheduler.running:
            self.scheduler.start()
            logger.info("Data refresh scheduler started with staggered schedule")
            # 根据策略自动创建刷新任务
            self.setup_auto_tasks()

    def stop(self):
        """停止调度器 (Stop scheduler)"""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Data refresh scheduler stopped")

    def add_task(
        self,
        task_func: Callable,
        task_name: str,
        trigger_type: str = 'cron',
        task_kwargs: dict = None,
        trading_time_only: bool | None = None,
        **trigger_args,
    ):
        """
        添加调度任务 (Add a scheduled task)
        """
        if task_kwargs is None:
            task_kwargs = {}
        if trading_time_only is None:
            trading_time_only = trigger_type == 'interval' and 'realtime' in task_name.lower()

        async def job_wrapper():
            # 只有在交易时间才运行高频任务 (Only run high-frequency tasks during trading time if appropriate)
            if trading_time_only:
                if not is_trading_time():
                    return

            task_id = str(uuid.uuid4())
            logger.info(f"Triggering scheduled task: {task_name} ({task_id})")
            runner_task_kwargs = dict(task_kwargs)
            runner_task_kwargs.setdefault("task_name", f"[Auto] {task_name}")

            # 提交到应用内异步任务运行器，阻塞 I/O 由任务内部的 run_in_executor 处理。
            async_task_runner.submit_task(
                task_id=task_id,
                task_func=task_func,
                task_kwargs=runner_task_kwargs,
                request_id=task_id,
                persist_status=False,
            )

        trigger = CronTrigger(**trigger_args) if trigger_type == 'cron' else IntervalTrigger(**trigger_args)
        self.scheduler.add_job(
            job_wrapper,
            trigger=trigger,
            id=f"auto_{task_name.lower().replace(' ', '_')}",
            name=task_name,
            replace_existing=True
        )
        logger.debug(f"Task scheduled: {task_name} via {trigger_type} {trigger_args}")

    def setup_auto_tasks(self):
        """
        根据设计方案自动创建错峰刷新任务 (Auto-create staggered refresh tasks based on design)
        """
        self.add_task(execute_daily_settlement_func, "T+1 Daily Settlement", hour=0, minute=1)
        # 1. 基础映射与低频数据
        self.add_task(sync_bulk_tables_func, "Stock Basic Info", hour=15, minute=5, task_kwargs={'tables': ['stocks']})
        self.add_task(sync_bulk_tables_func, "Industry Section Info", hour=15, minute=10, task_kwargs={'tables': ['industry']})
        # 2. 核心量价与盘后数据
        self.add_task(sync_bulk_tables_func, "Index Daily Kline", hour=15, minute=30, task_kwargs={'tables': ['index_daily']})
        self.add_task(sync_bulk_tables_func, "Stock Daily Kline Bulk", hour=15, minute=30, task_kwargs={'tables': ['kline']})
        self.add_task(calculate_indicators_func, "Technical Indicators Compute", hour=15, minute=45)

        # 3. 资金流动与情绪复盘
        self.add_task(sync_bulk_tables_func, "Limit Up/Down Pool", hour=15, minute=50, task_kwargs={'tables': ['stock_limit_up_pool', 'stock_limit_down_pool', 'stock_zhaban_pool']})
        self.add_task(sync_bulk_tables_func, "Sector Money Flow", hour=16, minute=10, task_kwargs={'tables': ['sector_money_flow']})
        self.add_task(sync_bulk_tables_func, "Stock Block Trade", hour=16, minute=30, task_kwargs={'tables': ['stock_block_trade']})
        self.add_task(sync_bulk_tables_func, "Dragon Tiger List", hour=17, minute=30, task_kwargs={'tables': ['dragontiger']})
        self.add_task(sync_bulk_tables_func, "Northbound Capital", hour=18, minute=0, task_kwargs={'tables': ['northbound']})
        self.add_task(sync_bulk_tables_func, "Margin Trading Security", hour=8, minute=10, task_kwargs={'tables': ['stock_margin_data']})

        # 4. 公司治理、表现与事件驱动
        self.add_task(sync_bulk_tables_func, "Stock Valuation History", hour=16, minute=0, task_kwargs={'tables': ['valuation']})
        self.add_task(sync_bulk_tables_func, "Pledge Risk Summary", hour=16, minute=45, task_kwargs={'tables': ['stock_pledge_summary']})
        self.add_task(sync_bulk_tables_func, "Top Shareholders Data", hour=16, minute=46, task_kwargs={'tables': ['stock_top_holders']})

        # 5. 高频监测与盘中动态
        self.add_task(
            sync_bulk_tables_func,
            "Realtime Quoter 1m",
            trigger_type='interval',
            minutes=1,
            task_kwargs={'tables': ['realtime']},
            trading_time_only=True,
        )
        self.add_task(
            cleanup_stock_realtime_market_history,
            "Stock Market Intraday Cache Cleanup 1h",
            trigger_type='interval',
            hours=1,
            trading_time_only=False,
        )

        logger.info("Auto refresh tasks scheduled and active")


# 创建全局调度器实例 (Create global scheduler instance)
refresh_scheduler = DataRefreshScheduler()
