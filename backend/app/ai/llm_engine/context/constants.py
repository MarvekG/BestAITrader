"""
Context Builder 数据评估标签常量
支持国际化,避免硬编码中文字符串
"""

# 十大股东持股集中度标签
CONCENTRATION_HIGH = "context.concentration.high"          # 高度集中
CONCENTRATION_MODERATE = "context.concentration.moderate"  # 适度集中
CONCENTRATION_DISPERSED = "context.concentration.dispersed"  # 较为分散

# 股东变动趋势标签
HOLDER_TREND_INCREASING = "context.holder_trend.increasing"  # 股东增持为主
HOLDER_TREND_DECREASING = "context.holder_trend.decreasing"  # 股东减持为主
HOLDER_TREND_STABLE = "context.holder_trend.stable"  # 持仓相对稳定

# 基金持仓强度标签
FUND_INTENSITY_STAR = "context.fund_intensity.star"  # 明星重仓股
FUND_INTENSITY_HIGH = "context.fund_intensity.high"  # 机构重点关注
FUND_INTENSITY_MODERATE = "context.fund_intensity.moderate"  # 有一定机构持仓
FUND_INTENSITY_LOW = "context.fund_intensity.low"  # 机构持仓较少

# 机构调研热度标签
SURVEY_HEAT_VERY_HIGH = "context.survey_heat.very_high"  # 调研热度极高
SURVEY_HEAT_HIGH = "context.survey_heat.high"  # 调研热度较高
SURVEY_HEAT_MODERATE = "context.survey_heat.moderate"  # 有机构关注
SURVEY_HEAT_LOW = "context.survey_heat.low"  # 调研较少

# 大宗交易意图标签
BLOCK_TRADE_INTENT_HIGH_DISCOUNT = "context.block_trade.high_discount"  # 明显折价
BLOCK_TRADE_INTENT_LOW_DISCOUNT = "context.block_trade.low_discount"  # 轻微折价
BLOCK_TRADE_INTENT_HIGH_PREMIUM = "context.block_trade.high_premium"  # 明显溢价
BLOCK_TRADE_INTENT_LOW_PREMIUM = "context.block_trade.low_premium"  # 轻微溢价
BLOCK_TRADE_INTENT_NEUTRAL = "context.block_trade.neutral"  # 平价交易,意图中性

# 大宗交易活跃度标签
BLOCK_TRADE_ACTIVITY_FREQUENT = "context.block_trade_activity.frequent"  # 交易频繁
BLOCK_TRADE_ACTIVITY_MODERATE = "context.block_trade_activity.moderate"  # 有一定交易
BLOCK_TRADE_ACTIVITY_LOW = "context.block_trade_activity.low"  # 交易较少

# 板块资金流状态标签
SECTOR_FLOW_HIGH_INFLOW = "context.sector_flow.high_inflow"  # 板块资金大幅流入
SECTOR_FLOW_INFLOW = "context.sector_flow.inflow"  # 板块资金流入
SECTOR_FLOW_HIGH_OUTFLOW = "context.sector_flow.high_outflow"  # 板块资金大幅流出
SECTOR_FLOW_OUTFLOW = "context.sector_flow.outflow"  # 板块资金流出
SECTOR_FLOW_BALANCED = "context.sector_flow.balanced"  # 板块资金相对平衡

# 联动分析提示
LINKAGE_HINT = "context.linkage.hint"  # 需结合个股资金流分析联动情况

# 回购态度标签
BUYBACK_ATTITUDE_FULFILLED = "context.buyback_attitude.fulfilled"  # 回购积极兑现
BUYBACK_ATTITUDE_ACTIVE = "context.buyback_attitude.active"  # 回购进展良好
BUYBACK_ATTITUDE_PROGRESSING = "context.buyback_attitude.progressing"  # 回购进行中
BUYBACK_ATTITUDE_ANNOUNCED = "context.buyback_attitude.announced"  # 仅公告回购

# 回购价格安全边际标签
BUYBACK_PRICE_SAFE = "context.buyback_price.safe"  # 股价远低于回购上限(安全边际大)
BUYBACK_PRICE_MODERATE = "context.buyback_price.moderate"  # 股价低于回购上限
BUYBACK_PRICE_RISK = "context.buyback_price.risk"  # 股价接近或超过回购上限

# 基金持仓确信度标签
FUND_CONVICTION_HIGH = "context.fund_conviction.high"  # 基金顶格配置(极高确信度)
FUND_CONVICTION_MODERATE = "context.fund_conviction.moderate"  # 基金重仓持有
FUND_CONVICTION_LOW = "context.fund_conviction.low"  # 基金标配

# 调研人员规格标签
SURVEY_LEVEL_HIGH = "context.survey_level.high"  # 董事长/CEO亲自出席
SURVEY_LEVEL_MODERATE = "context.survey_level.moderate"  # 高管出席
SURVEY_LEVEL_NORMAL = "context.survey_level.normal"  # 证代/其他人员出席
