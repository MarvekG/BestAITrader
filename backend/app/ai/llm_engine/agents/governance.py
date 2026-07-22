from typing import List, Any
from langchain_core.tools import tool
from app.ai.llm_engine.agents.base import BaseAgent
from app.ai.llm_engine.prompts import templates
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER
from app.ai.agentic.tools import (
    execute_trading_order as execute_trading_order_core,
    get_pm_order_type_guidance,
)
from app.ai.llm_engine.pm_decision_service import save_pm_decision_record
from app.ai.llm_engine.position_plan_service import (
    calculate_executable_position_plan as calculate_executable_position_plan_service,
)


class PortfolioManagerAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_PORTFOLIO_MANAGER, model_name=model_name, **kwargs)

    def get_tools(self) -> List[Any]:
        """
        获取投资经理专属工具集。
        动态包装 execute_trading_order，隐藏 session_id 参数。
        """
        tools = super().get_tools()

        # 通过闭包注入会话 ID，避免 LLM 传递内部关联参数。
        @tool
        async def execute_trading_order(
            stock_code: str = "",
            action: str = "buy",
            target_position: float = 0.0,
            stop_loss: float = 0.0,
            take_profit: float = 0.0,
            operation: str = "place",
            order_type: str = "market",
            limit_price: float | None = None,
            order_id: str | None = None,
        ):
            """
            执行股票交易下单工具 (Execute stock trading order).

            该工具由投资经理(Portfolio Manager)在做出决策后调用，用于将决策转化为模拟交易订单。

            参数:
            - stock_code: 股票代码，如 '600519.SH'
            - action: 'buy' 或 'sell'
            - target_position: 目标仓位比例 (0.0 - 1.0)。
              - 如果是 'buy'，则计算达到目标比例所需买入的股数。
              - 如果是 'sell'，则计算达到目标比例所需卖出的股数。
              - target_position = 0 即为全额清仓。
            - stop_loss: 最终止损价，必填。系统会在成交后直接写入持仓。
            - take_profit: 最终止盈价或目标价，必填。买入时必须高于当前价。
            - operation: place 表示下单，cancel 表示撤销待成交订单。
            - order_type: market 表示市价单，limit 表示限价挂单。
            - limit_price: 限价挂单委托价，限价单必填。
            - order_id: 撤单目标订单 ID，撤单必填。

            注意:
            1. 自动执行 A 股交易规则：买入必须是 100 的整数倍；卖出减仓时尽量取 100 倍数。
            2. 自动检查 T+1 可用持仓限制，卖出数量不会超过可用股数。
            3. 仅在系统配置的 ENABLE_AUTO_TRADE 为开启状态时写入模拟交易账本。
            4. `stop_loss` 为必填字段，必须与本轮最终止损纪律保持一致。
            5. 若工具返回 `success=false`，你必须先根据返回的 `reason`
               判断是否要调整 `target_position` 后再次调用，或停止交易并输出最终结论。
            """
            return await execute_trading_order_core(
                stock_code=stock_code,
                action=action,
                target_position=target_position,
                session_id=self.session_id,
                stop_loss=stop_loss,
                take_profit=take_profit,
                operation=operation,
                order_type=order_type,
                limit_price=limit_price,
                order_id=order_id,
            )

        @tool
        async def calculate_executable_position_plan(target_position: float):
            """计算目标仓位按当前账户和 A 股整手规则能否执行。

            Args:
                target_position: 交易完成后的绝对目标仓位，范围为 0 到 1。

            Returns:
                自动读取当前会话、账户、行情、持仓和待成交订单后得到的整手数量、实际目标仓位及不可执行原因。
            """
            return await calculate_executable_position_plan_service(
                session_id=self.session_id,
                target_position=target_position,
            )

        @tool
        async def save_pm_decision(
            target_position: float = 0.0,
            confidence_score: float = 0.0,
            stop_loss: float | None = None,
            take_profit: float | None = None,
            holding_horizon_days: int | None = None,
        ):
            """
            保存 PM 最小结构化决策字段。

            参数:
            - target_position: 操作完成后的目标仓位比例，范围 0 到 1。
            - confidence_score: 本次决策置信度，范围 0 到 100。
            - stop_loss: 止损或复议价格；无持仓或不适用时可留空。
            - take_profit: 止盈或目标价格；无持仓或不适用时可留空。
            - holding_horizon_days: 预期持有或复议周期天数；不适用时可留空。
            """
            try:
                record = await save_pm_decision_record(
                    session_id=self.session_id,
                    target_position=target_position,
                    confidence_score=confidence_score,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    holding_horizon_days=holding_horizon_days,
                )
                return {
                    "success": True,
                    "message": "PM structured decision saved.",
                    "decision": record,
                }
            except Exception as exc:
                return {
                    "success": False,
                    "message": str(exc),
                    "reason": "pm_decision_save_failed",
                }

        tools.append(get_pm_order_type_guidance)
        tools.append(calculate_executable_position_plan)
        tools.append(save_pm_decision)
        tools.append(execute_trading_order)
        return tools

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "PORTFOLIO_MANAGER",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self):
        return str

    async def get_final_output_feedback(self, final_content: str) -> str | None:
        """确保 PM 最终报告前已保存本轮结构化纪律字段。

        Args:
            final_content: PM 最终 Markdown 报告。

        Returns:
            已保存时返回 None；未保存时返回要求继续调用工具的反馈。

        Raises:
            ValueError: 缺少会话 ID 时抛出。
        """
        if not self.session_id:
            raise ValueError("PM structured decision requires session_id")

        from app.ai.llm_engine.pm_decision_service import get_pm_decision_for_session
        pm_record = await get_pm_decision_for_session(self.session_id)
        if pm_record:
            return None
        return (
            "你的最终 Markdown 报告暂不能接受：本轮 PM 结构化纪律字段尚未保存。"
            "你必须先调用 `save_pm_decision` 工具，写入 `target_position`、`confidence_score`、"
            "`stop_loss`、`take_profit`、`holding_horizon_days`，然后再输出最终 Markdown 报告。"
            "不要改用 JSON 最终输出。\n"
            "Your final Markdown report cannot be accepted yet because the PM structured discipline fields "
            "have not been saved. Call the `save_pm_decision` tool first, then provide the final Markdown report."
        )
