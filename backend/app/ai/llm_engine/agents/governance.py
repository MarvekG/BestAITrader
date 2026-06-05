from typing import Type, List, Any
from langchain_core.tools import tool
from app.ai.llm_engine.agents.base import BaseAgent
from app.ai.llm_engine.models import PMDecision
from app.ai.llm_engine.prompts import templates
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER
from app.ai.agentic.tools import execute_trading_order as execute_trading_order_core


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
            stock_code: str,
            action: str,
            target_position: float,
            stop_loss: float,
            take_profit: float,
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
            )

        tools.append(execute_trading_order)
        return tools

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "PORTFOLIO_MANAGER",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[PMDecision]:
        return PMDecision
