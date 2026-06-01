from __future__ import annotations

from typing import Literal, Sequence

ReviewHorizon = Literal["5d", "20d", "60d"]

REVIEW_HORIZONS: tuple[ReviewHorizon, ...] = ("5d", "20d", "60d")
HORIZON_REQUIRED_MARKET_DAYS: dict[ReviewHorizon, int] = {
    "5d": 6,
    "20d": 21,
    "60d": 61,
}
HORIZON_PRIORITY: dict[ReviewHorizon, int] = {
    "5d": 1,
    "20d": 2,
    "60d": 3,
}


def normalize_review_horizon(value: str | None) -> ReviewHorizon | None:
    """规范化用户提供的复盘周期。

    Args:
        value: API 入参、事件 payload 或内部调用传入的原始周期值。

    Returns:
        规范化后的复盘周期；未提供周期时返回 ``None``。

    Raises:
        ValueError: 当传入值不是支持的复盘周期时抛出。
    """
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in REVIEW_HORIZONS:
        return normalized  # type: ignore[return-value]
    raise ValueError(f"Unsupported review horizon: {value}")


def eligible_horizons(market_day_count: int | None) -> list[ReviewHorizon]:
    """返回已有足够决策后日 K 样本的复盘周期。

    Args:
        market_day_count: PM 决策后可用的日 K 样本数量。

    Returns:
        当前市场数据足以评估的复盘周期列表。
    """
    count = max(0, int(market_day_count or 0))
    return [
        horizon
        for horizon in REVIEW_HORIZONS
        if count >= HORIZON_REQUIRED_MARKET_DAYS[horizon]
    ]


def highest_horizon(horizons: Sequence[str]) -> ReviewHorizon | None:
    """从复盘周期字符串中返回优先级最高的周期。

    Args:
        horizons: 调用方传入的候选周期值，顺序不影响结果。

    Returns:
        优先级最高的规范化周期；没有有效周期时返回 ``None``。

    Raises:
        ValueError: 当任一周期值不受支持时抛出。
    """
    normalized: list[ReviewHorizon] = []
    for horizon in horizons:
        parsed = normalize_review_horizon(horizon)
        if parsed is not None:
            normalized.append(parsed)
    if not normalized:
        return None
    return max(normalized, key=lambda item: HORIZON_PRIORITY[item])


def horizon_gap(market_day_count: int | None, horizon: ReviewHorizon) -> int:
    """返回目标周期仍需补足的日 K 样本数量。

    Args:
        market_day_count: 当前可用的决策后日 K 样本数量。
        horizon: 需要评估的复盘周期。

    Returns:
        目标周期达到可复盘状态前仍需补足的样本数量。
    """
    count = max(0, int(market_day_count or 0))
    return max(0, HORIZON_REQUIRED_MARKET_DAYS[horizon] - count)


def review_status_for_candidate(
    *,
    eligible: Sequence[str],
    completed: Sequence[str],
    active: Sequence[str],
    failed: Sequence[str],
) -> str:
    """计算复盘候选项的展示状态。

    Args:
        eligible: 当前市场数据已经满足的周期。
        completed: 已完成经验复盘事件的周期。
        active: 正在复盘中的周期。
        failed: 最近一次复盘事件失败的周期。

    Returns:
        经验复盘 UI 使用的候选项展示状态。

    Raises:
        ValueError: 当任一周期序列包含不受支持的值时抛出。
    """
    eligible_set = {item for item in (normalize_review_horizon(value) for value in eligible) if item is not None}
    completed_set = {item for item in (normalize_review_horizon(value) for value in completed) if item is not None}
    active_set = {item for item in (normalize_review_horizon(value) for value in active) if item is not None}
    failed_set = {item for item in (normalize_review_horizon(value) for value in failed) if item is not None}

    if active_set:
        return "reviewing"

    ready = eligible_set - completed_set
    ready_horizon = highest_horizon(list(ready))
    if ready_horizon:
        return f"ready_{ready_horizon}"

    if eligible_set and eligible_set.issubset(completed_set):
        return "reviewed"

    if failed_set:
        return "failed"

    return "not_ready"
