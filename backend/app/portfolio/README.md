# Portfolio 模块设计与约束

`portfolio` 负责组合视角的估值和展示数据组织。它依赖交易账本和行情数据，但不直接创建订单，也不替代风控模块。

## 职责

- 基于账户、现金、持仓、行情和成本构建组合估值。
- 为前端组合概览提供稳定的数据结构。
- 与绩效、风控共享一致的持仓和账户语义。

## 设计约束

- 组合估值复用 `build_portfolio_valuation`，不要在页面或其他 service 里重复计算一套估值。
- 不直接修改订单、成交和持仓；写操作必须进入交易服务。
- 行情缺失时暴露缺失或估值降级状态，不把旧价格伪装为最新价格。
- 组合视角只做当前状态和派生指标，不做后验复盘，复盘归 `ai/experience`。

## 修改入口

- 组合服务：`service.py`
- 估值计算：`valuation.py`
- 前端设计记录：`PORTFOLIO_OVERVIEW_TAB_DESIGN.md`

## 验证

- 修改估值或组合接口时运行 portfolio/trading/performance 相邻测试。
