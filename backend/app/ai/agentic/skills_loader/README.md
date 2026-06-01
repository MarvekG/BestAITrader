# Skills Loader Integration Guide

本文档说明 `backend/app/ai/agentic/skills_loader` 是什么、为什么推荐按需补齐 Skills、如何新增一个高质量 Skill，以及 AI 在看到本文档后应该如何完成集成、验证和排障。

Skills 的目标不是把已有 Python 函数换个名字暴露给 LLM，而是把一类专业能力封装成“LLM 可阅读、可按步骤执行、可查参考资料、可调用脚本”的能力包。对于天枢智投（Best-AI-Trader），这类能力尤其适合承载外部数据接口、行业知识库、投研方法论、财报字段解释、公告解析规则、风格化分析流程、专有数据库查询方式等内容。

正式使用系统前，推荐按需补齐自己的 Skills。当前不补充额外 Skills，系统仍然可以依赖内置工具、上下文、新闻插件和已有数据链路完成正常分析；补齐 Skills 的价值在于把团队自己的数据授权、字段含义、内部口径、业务偏好和专有分析流程沉淀成可复用能力，从而进一步提升专业深度和稳定性。

## 1. 系统定位

Skills Loader 提供四个层面的能力：

| 层级 | 作用 | 关键文件 |
| --- | --- | --- |
| 发现 | 扫描本地安装的 skill 包，生成可用能力目录 | `loader.py` |
| 提示 | 把轻量 skill catalog 注入 Agent system prompt | `runtime.py` |
| 读取 | 让 Agent 按需读取 `SKILL.md` 和 references | `skill_tools.py` |
| 执行 | 让 Agent 安全执行 skill 内 `scripts/` 下的脚本 | `skill_tools.py` |

Agent 的正确使用链路是：

```text
看到 catalog
  -> 选择相关 skill
  -> 调用 load_skill 读取完整 SKILL.md
  -> 按 SKILL.md 读取 references
  -> 必要时调用 run_skill_script
  -> 把脚本结果、参考资料和已有工具结果合并进分析
```

不要让 Agent 只看到 skill 名称就直接猜用法。`runtime.py` 注入的是轻量目录，不是完整说明；完整说明必须通过 `load_skill` 获取。

## 2. 放置位置

所有本地 Skills 放在：

```text
backend/app/ai/agentic/skills_loader/skills/
```

每个 skill 是一个独立目录：

```text
skills/
  my-skill/
    skill.json
    SKILL.md
    references/
    scripts/
```

必需文件：

| 文件 | 用途 |
| --- | --- |
| `skill.json` | 机器可读摘要，供 Loader 发现和 catalog 展示 |
| `SKILL.md` | LLM 阅读的完整操作手册，是最重要的文件 |

可选目录：

| 目录 | 用途 |
| --- | --- |
| `references/` | 接口文档、字段说明、业务规则、样例、决策口径等静态资料 |
| `scripts/` | 可执行脚本或二进制入口，供 `run_skill_script` 调用 |

当前仓库内置示例：

```text
backend/app/ai/agentic/skills_loader/skills/tushare-data/
```

新增 Skill 时，不要把所有东西塞进 `SKILL.md`。`SKILL.md` 应该写操作逻辑和索引；长表格、接口清单、字段字典、样例响应、行业知识等放入 `references/`。

## 3. 系统设置界面管理

系统设置页提供独立的 Skills 管理 tab，位置在“新闻插件管理”旁边。该界面用于运行时添加和删除 Skill，不需要手工进入容器改文件。

管理接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/skills` | 列出当前 loader 可发现的 Skills |
| `POST` | `/skills` | 上传一个 Skill 文件夹 |
| `DELETE` | `/skills/{skill_id}` | 删除一个已安装 Skill 文件夹 |

上传要求：

- 上传对象必须是一个文件夹，不是单个文件。
- 一次只上传一个 Skill 文件夹。
- 文件夹根目录必须包含 `skill.json`。
- 文件夹根目录必须包含 `SKILL.md`。
- `skill.json` 必须是 UTF-8 JSON 对象，并包含 `name` 和 `description`。
- 文件夹名就是 `skill_id`，只允许字母、数字、`_`、`-`，长度不超过 64。
- 上传路径不能包含 `..`，不能逃逸出 Skill 根目录。

上传成功后，后端会写入：

```text
backend/app/ai/agentic/skills_loader/skills/<skill_id>/
```

上传同名 `skill_id` 时会替换原目录。删除时会删除对应的 `<skill_id>/` 目录。

LLM 是否能选到上传后的 Skill：

- 能。`discover_skills()` 每次运行时扫描 `skills/` 目录，不依赖服务重启。
- Debate、AI 智能选股、经验复盘等已接入 Skills Loader 的链路，会在下一次任务启动时看到新的 catalog 摘要。
- Agent 只会先看到 `skill_id`、`name`、`description`、references 和 scripts 清单；真正使用前仍应调用 `load_skill` 读取完整 `SKILL.md`。
- 如果当前问题和 Skill 描述不相关，LLM 可能不会主动选择该 Skill。这是预期行为。

上传目录属于运行时资产。`skills/.gitignore` 默认忽略运行时上传的 Skill，避免把私有资料、脚本和临时测试目录提交进仓库。当前仓库只显式保留内置 `tushare-data`。

开发环境启用 uvicorn reload 时，`backend/run.py` 已排除 `app/ai/agentic/skills_loader/skills/*`，上传或删除 Skill 不会触发后端自动重载。

## 4. 推荐补齐什么

这些 Skills 不是系统正常分析的硬性前置条件，而是增强 AI 专业能力的推荐扩展项：

| Skill 类型 | 为什么重要 | 示例 |
| --- | --- | --- |
| 数据接口 Skill | 让 Agent 知道哪些接口可用、参数是什么、返回字段怎么解释 | Tushare、Wind、聚宽、内部行情库 |
| 公告解析 Skill | 上市公司公告字段、类型、事件含义高度专业 | 年报、业绩预告、回购、减持、质押、诉讼 |
| 行业研究 Skill | 通用 LLM 不知道你的行业分类和跟踪框架 | 半导体、创新药、白酒、地产链、AI 算力 |
| 风控规则 Skill | 仓位、止损、黑名单、异常事件需要稳定规则 | ST、退市风险、流动性、解禁、商誉减值 |
| 投研流程 Skill | 将团队的分析方法固化成可重复步骤 | 主题研究、事件驱动、财报快评、估值比较 |
| 内部知识 Skill | 公司内部专有口径不能靠模型猜 | 自建指标、内部评级、策略标签 |

判断是否应该做成 Skill：

- 如果一类知识会被反复使用，做成 Skill。
- 如果 Agent 容易记错字段、接口或流程，做成 Skill。
- 如果需要先读文档再执行脚本，做成 Skill。
- 如果只是一次性业务参数，不要做成 Skill，直接放用户输入或普通配置。

## 5. `skill.json` 规范

当前 Loader 强制要求两个字段：

```json
{
  "name": "my-skill",
  "description": "Describe what this skill helps the LLM do."
}
```

推荐扩展字段：

```json
{
  "name": "my-skill",
  "description": "用于查询和解释某类投研数据。",
  "domains": ["A股", "公告", "行业研究"],
  "owner": "research-platform",
  "version": "1.0.0"
}
```

`domains`、`owner`、`version` 当前代码不使用，也不会影响 Loader 发现、Agent catalog 展示或脚本执行；它们仅供团队管理、资产盘点、版本追踪和人工维护时参考。如果团队不需要这些治理信息，可以不写。

字段建议：

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 展示名称，建议和目录名一致 |
| `description` | 是 | 一句话描述能力边界，不要写成长文 |
| `domains` | 否 | 当前代码不使用，仅供团队管理；标注适用领域 |
| `owner` | 否 | 当前代码不使用，仅供团队管理；标注维护者或团队 |
| `version` | 否 | 当前代码不使用，仅供团队管理；标注 Skill 自身版本 |

`description` 要写清楚“能做什么”和“不能做什么”。坏例子：

```json
{
  "description": "很强的数据分析 skill"
}
```

好例子：

```json
{
  "description": "查询 Tushare A 股行情、财务、资金流和公告相关接口，并按接口文档解释字段含义。"
}
```

## 6. `SKILL.md` 编写规范

`SKILL.md` 是给 LLM 看的完整说明。它必须让 Agent 独立判断：

- 什么时候应该使用这个 Skill。
- 什么时候不应该使用这个 Skill。
- 使用前应该读取哪些 references。
- 可以调用哪些 scripts。
- 每个脚本接受什么输入。
- 每个脚本返回什么输出。
- 出错时如何降级。
- 哪些情况需要向用户澄清。
- 哪些结果可以作为证据，哪些只能作为线索。

推荐结构：

```markdown
# my-skill

## 什么时候使用

## 不要在这些场景使用

## 工作流

## References

## Scripts

## 输入输出协议

## 失败和降级

## 证据使用规则

## 示例
```

### 5.1 什么时候使用

写成明确触发条件：

```markdown
当用户或 Agent 需要查询 A 股日线、财务指标、资金流、龙虎榜、股东人数、回购、业绩预告或基础资料时，使用本 Skill。
```

不要写模糊描述：

```markdown
需要数据时使用。
```

### 5.2 不要在这些场景使用

每个 Skill 都要写边界。例子：

```markdown
不要用本 Skill 搜索实时新闻；新闻请使用 search_news。
不要用本 Skill 生成交易指令；交易由 PM Agent 的交易工具处理。
不要把接口返回字段臆测为事实；字段含义必须先查 references。
```

### 5.3 工作流

写成可执行步骤。例子：

```markdown
1. 判断用户问题需要哪类数据。
2. 先读取 `references/数据接口.md`，确定接口名、参数和字段。
3. 使用 `run_skill_script` 调用 `scripts/call_tushare.py`。
4. 检查 stdout 中的 `success` 字段。
5. 如果成功，引用返回数据并说明接口名和关键参数。
6. 如果失败，说明失败原因，并回退到已有数据库工具或请求用户补充信息。
```

### 5.4 References

列清楚每个参考文件什么时候读：

```markdown
- `references/fields.md`：字段字典。解释返回字段前必须读取。
- `references/examples.md`：典型请求与响应。脚本调用前建议读取。
- `references/business-rules.md`：业务口径。涉及策略结论时必须读取。
```

### 5.5 Scripts

每个脚本必须写：

- 脚本路径。
- 用途。
- 输入格式。
- 输出格式。
- 成功示例。
- 失败示例。
- 超时时间建议。

示例：

````markdown
### `scripts/query.py`

用途：查询内部数据接口。

调用：

```json
{
  "skill_id": "my-skill",
  "command": ["python", "scripts/query.py"],
  "stdin": "{\"symbol\":\"600519.SH\",\"fields\":[\"close\",\"amount\"]}",
  "timeout_seconds": 120
}
```

成功输出：

```json
{
  "success": true,
  "data": [],
  "source": "internal_api"
}
```
````

## 7. References 设计

`references/` 是让 LLM 获得稳定知识的地方。推荐按用途拆分：

```text
references/
  README.md
  interfaces.md
  fields.md
  examples.md
  business-rules.md
  troubleshooting.md
```

拆分原则：

- `SKILL.md` 放流程，不放超长字段表。
- 字段表、接口表、样例响应放 references。
- 每个 reference 文件开头说明“这个文件是干什么的”。
- 文件名要稳定，避免 Agent 找不到。
- 一个 reference 文件只讲一类事情。

字段字典建议格式：

```markdown
| 字段 | 类型 | 含义 | 注意事项 |
| --- | --- | --- | --- |
| close | float | 收盘价 | 复权口径需看接口参数 |
| amount | float | 成交额 | 单位由接口定义 |
```

业务规则建议格式：

```markdown
## 回购事件解读

- 大额回购通常偏正面，但需要结合回购价格上限、资金来源和历史执行率。
- 董监高减持期内的回购不应直接视为强信号。
- 回购预案和实际实施进度需要分开判断。
```

## 8. Scripts 协议

脚本优先支持 stdin，因为 stdin 最适合传 JSON、URL、自然语言和大块参数。

推荐协议：

- 从 stdin 读取 JSON object。
- 向 stdout 输出 JSON object。
- 把诊断信息写 stderr。
- 退出码 `0` 表示脚本正常执行。
- 非 `0` 表示脚本级失败。

成功输出：

```json
{
  "success": true,
  "result": {},
  "source": "my-skill"
}
```

失败输出：

```json
{
  "success": false,
  "error": "message",
  "source": "my-skill"
}
```

脚本不要直接输出给人看的长段落。应尽量输出结构化 JSON，让 Agent 再负责解释。

## 9. `run_skill_script` 调用规则

`run_skill_script` 接受：

```json
{
  "skill_id": "my-skill",
  "command": ["python", "scripts/query.py"],
  "stdin": "{}",
  "timeout_seconds": 120
}
```

允许命令：

- `["python", "scripts/tool.py", "--flag", "value"]`
- `["python3", "scripts/tool.py", "--flag", "value"]`
- `["bash", "scripts/tool.sh", "--flag", "value"]`
- `["sh", "scripts/tool.sh", "--flag", "value"]`
- `["scripts/tool_binary", "--flag", "value"]`

禁止命令：

- `["python", "-c", "..."]`
- `["python", "-m", "module"]`
- `["sh", "-c", "..."]`
- `["bash", "-c", "..."]`
- 入口不在 `scripts/` 目录下的命令。

`command` 每一项都会拒绝常见 shell 注入元字符，例如 `;`、`|`、`&`、`` ` ``、`$`、`<`、`>` 和换行。复杂 JSON、URL query、SQL、自然语言文本等内容必须放入 `stdin`。

## 10. 最小 Skill 模板

目录：

```text
backend/app/ai/agentic/skills_loader/skills/my-skill/
  skill.json
  SKILL.md
  references/interfaces.md
  scripts/query.py
```

`skill.json`：

```json
{
  "name": "my-skill",
  "description": "查询某类投研数据，并解释字段和业务口径。"
}
```

`SKILL.md`：

````markdown
# my-skill

## 什么时候使用

当问题需要查询某类专有投研数据时使用。

## 不要在这些场景使用

- 不要用于新闻搜索。
- 不要用于下单、撤单或真实交易。

## 工作流

1. 读取 `references/interfaces.md`。
2. 确定接口、参数和字段。
3. 调用 `scripts/query.py`。
4. 检查输出中的 `success`。
5. 只把返回数据作为证据，不要臆测缺失字段。

## Scripts

调用示例：

```json
{
  "skill_id": "my-skill",
  "command": ["python", "scripts/query.py"],
  "stdin": "{\"symbol\":\"600519.SH\"}",
  "timeout_seconds": 120
}
```
````

`scripts/query.py`：

```python
"""Query example skill data."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def main() -> int:
    """Run the query script."""
    payload: dict[str, Any] = json.loads(sys.stdin.read() or "{}")
    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        print(json.dumps({"success": False, "error": "symbol is required"}, ensure_ascii=False))
        return 1

    api_key = os.getenv("MY_DATA_API_KEY", "")
    if not api_key:
        print(json.dumps({"success": False, "error": "MY_DATA_API_KEY is not configured"}, ensure_ascii=False))
        return 1

    result = {"symbol": symbol, "items": []}
    print(json.dumps({"success": True, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## 11. 新增 Skill 标准流程

1. 明确能力边界。
2. 创建目录。
3. 编写 `skill.json`。
4. 编写 `SKILL.md`。
5. 把字段表、接口表、业务规则拆到 `references/`。
6. 如果需要确定性动作，编写 `scripts/`。
7. 确认脚本只读 stdin、只写结构化 stdout。
8. 如果脚本需要新增第三方依赖，更新本目录的 `requirements.txt`。
9. 运行发现和读取验证。
10. 运行脚本验证。
11. 让 AI-IDE 跑一次完整 Skills 链路，确认新的 Skill 能被发现、加载、读取 reference，并按文档调用脚本。

## 12. Git 规则

默认 `skills/` 目录忽略外部安装的 Skill。当前仓库显式允许 `tushare-data` 入库。

新增需要入库的 Skill 时，需要同步调整：

```text
backend/app/ai/agentic/skills_loader/skills/.gitignore
```

如果 Skill 绑定团队私有资料，不建议直接提交到公开仓库。可以使用私有子模块、私有包或部署时挂载目录，但需要保证运行路径仍然是：

```text
backend/app/ai/agentic/skills_loader/skills/<skill_id>/
```

## 13. Python 依赖管理

Skills 的 Python 依赖独立维护在：

```text
backend/app/ai/agentic/skills_loader/requirements.txt
```

Docker 构建后端镜像时会同时安装：

```text
backend/requirements.txt
backend/app/ai/agentic/tooling/news_plugins/requirements.txt
backend/app/ai/agentic/skills_loader/requirements.txt
```

新增 Skill 脚本如果需要第三方库，优先修改 `backend/app/ai/agentic/skills_loader/requirements.txt`，不要把 Skill 专用依赖混进主后端依赖文件。当前内置 `tushare-data` 使用的是后端已有 Tushare 数据源能力，不需要额外 Skill 专用依赖。

上传 Skill 时，如果 Skill 根目录包含 `requirements.txt`，后端会先执行：

```bash
python -m pip install --user -r <临时 requirements 文件>
```

依赖安装成功后才会保存或替换 Skill；安装失败时上传失败，原有 Skill 目录不变。该方式只影响当前容器环境。
由于依赖会安装到后端容器当前应用用户的 Python user site 中，`requirements.txt` 默认不要指定版本号，尽量复用容器内已有版本，
避免覆盖主系统、新闻插件或其他 Skills 正在使用的依赖。只有在 Skill 明确依赖某个 API 且已验证不会影响现有系统时，
才考虑写版本约束。
需要在重建镜像后仍然生效时，应同步更新 `backend/app/ai/agentic/skills_loader/requirements.txt` 并重新构建后端镜像。

## 14. 质量要求

一个可用于正式系统的 Skill 至少应满足：

- `SKILL.md` 能让 Agent 独立完成操作。
- 每个 reference 文件开头说明用途。
- 每个脚本都有输入输出示例。
- 脚本失败时返回结构化错误。
- 不硬编码密钥。
- 不做交易副作用。
- 不绕过 `scripts/` 执行限制。
- 不依赖未写入 `backend/app/ai/agentic/skills_loader/requirements.txt` 的 Skill 专用库。
- 长输出要截断或分页，避免挤爆上下文。
- 文档中的示例不要和测试样本完全一样，避免对记忆系统或评测产生定制化污染。

## 15. 给 AI 的集成指令

当 AI 需要新增或修改 Skill 时，按以下顺序执行：

1. 先读本 README。
2. 再读现有 `tushare-data` 作为参考。
3. 确认目标能力是否适合做成 Skill。
4. 设计目录、manifest、SKILL.md、references、scripts。
5. 实现脚本时保持业务逻辑与 I/O 分离。
6. 用 `run_skill_script` 的真实限制验证命令。
7. 如需新增第三方依赖，更新本目录的 `requirements.txt`。
8. 运行验证命令。
9. 在最终说明里列出新增 Skill、用途、依赖、环境变量和验证结果。
