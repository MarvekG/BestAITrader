# Agentic 工具体系设计与约束

`agentic` 为 Agent 提供受控工具能力。这里的重点不是扩大工具数量，而是保证工具来源明确、权限受限、输出可审计，并且不会让模型绕过业务边界。

## 职责

- 提供股票数据、市场数据、数据库查询、同步数据、计算沙箱和交易工具包装。
- 通过 `tooling/news_tool.py` 和 `tooling/news_plugins/` 接入多源新闻。
- 通过 `tooling/python_sandbox.py` 调用独立沙箱服务。
- 通过 browser/PDF 工具调用独立 WebFetch 服务。
- 通过 Skills Loader 让 Agent 按需读取专业技能文档和脚本。
- 通过 Memory tools 绑定用户和股票 scope，召回或写入 MemoFlux。

## 设计约束

- 新增新闻源必须按 `tooling/news_plugins/README.md` 做插件，一个插件代表一个明确来源。
- `search_news` 调用要保留来源，避免多源证据混在一起。
- Skills 使用必须先 `load_skill`，再读取 references/scripts；不能只看 catalog 猜行为。
- Python 沙箱只用于受限计算，不提供文件、网络、进程或宿主环境访问。
- 长工具输出要经过摘要或裁剪，避免挤占 Agent 推理上下文。
- 交易工具只挂给 PM 相关 Agent，不能作为通用工具暴露给分析师。
- Memory 写入必须有明确复用价值和 scope，不能把一次性流水账写成长记忆。

## 修改入口

- 通用工具：`tools.py`
- 新闻工具：`tooling/news_tool.py`、`tooling/news_plugins/`
- 沙箱：`tooling/python_sandbox.py`
- 浏览器/PDF：`tooling/browser_tool.py`、`tooling/pdf_tool.py`
- Skills：`skills_loader/`
- Memory：`memory_tools.py`

## 验证

- 工具安全或插件变更运行相关 `test_agentic_*`。
- 外部服务调用边界变更后运行系统自检接口。
