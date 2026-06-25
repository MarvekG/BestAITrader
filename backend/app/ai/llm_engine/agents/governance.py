from typing import List, Any
from langchain_core.tools import tool
from app.ai.llm_engine.agents.base import BaseAgent
from app.ai.llm_engine.prompts import templates
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER
from app.ai.agentic.tools import (
    execute_trading_order as execute_trading_order_core,
    get_pm_order_type_guidance,
    save_pm_decision as save_pm_decision_core,
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

        # 定义一个闭包工具，LLM 只需看到必要的三个参数
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
            return await save_pm_decision_core(
                session_id=self.session_id,
                target_position=target_position,
                confidence_score=confidence_score,
                stop_loss=stop_loss,
                take_profit=take_profit,
                holding_horizon_days=holding_horizon_days,
            )

        tools.append(get_pm_order_type_guidance)
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
