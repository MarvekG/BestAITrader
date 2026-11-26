from __future__ import annotations

from app.tasks.scheduled_task_registry import ScheduledTask, ScheduledTaskSnapshot

DEBATE_DISPATCH_JOB_ID = "debate_queue_dispatch"
DEBATE_DISPATCH_INTERVAL_SECONDS = 60


async def dispatch_queued_debates() -> None:
    """补偿调度排队中的 AI 投研辩论任务。

    正常调度由提交、完成和运行参数变更触发；本任务定期兜底，避免单次调度
    异常后队列长期停留在 pending。
    """
    from app.ai.llm_engine.debate_concurrency import dispatch_pending_debate_tasks_safely

    await dispatch_pending_debate_tasks_safely()


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """返回辩论队列补偿调度任务定义。

    Returns:
        包含每分钟补偿调度任务的调度快照。
    """
    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=dispatch_queued_debates,
                task_name="Debate Queue Dispatch",
                trigger_type="interval",
                job_id=DEBATE_DISPATCH_JOB_ID,
                trigger_args={"seconds": DEBATE_DISPATCH_INTERVAL_SECONDS},
                misfire_grace_time=DEBATE_DISPATCH_INTERVAL_SECONDS,
            )
        ],
        disabled_job_ids=[],
    )
