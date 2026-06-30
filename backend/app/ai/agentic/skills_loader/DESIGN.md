# Agentic Skills Loader Design

## 目标

为股票分析系统增加外部 skills 加载能力。系统不把现有工具改造成 skill，而是扫描本地安装的外部
skill 包，例如 `waditu-tushare/skills` 的 `tushare-data`，把可用 skill 摘要暴露给现有 Agent。
主 Agent 在原有工具调用循环中自行决定是否调用 `load_skill`、读取 reference 或执行 skill script。

## 目录约定

Loader 代码和本地 skills 都放在 `backend/app/ai/agentic/skills_loader/`：

```text
backend/app/ai/agentic/skills_loader/
  __init__.py
  loader.py
  runtime.py
  skill_tools.py
  skills/
    tushare-data/
      skill.json
      SKILL.md
      references/
      scripts/
```

安装外部 skill 的推荐方式：

```bash
mkdir -p backend/app/ai/agentic/skills_loader/skills
cd backend/app/ai/agentic/skills_loader/skills
npx skills add https://github.com/waditu-tushare/skills.git --skill tushare-data
```

系统设置页支持上传 Skill 目录；如果目录根部包含 `requirements.txt`，后端会在保存前用当前应用用户执行
`python -m pip install --user -r <临时 requirements 文件>` 安装依赖。

## 运行机制

1. `loader.py` 扫描 `skills/*/skill.json` 和 `skills/*/SKILL.md`。`skill.json` 必须是 JSON 对象，
   且必须包含 `name` 和 `description`；无效 skill 直接跳过。
2. `runtime.py` 生成轻量 catalog prompt，只注入可用 skill 摘要，不直接注入完整 `SKILL.md`。
3. `skill_tools.py` 暴露 LangChain tools：
   - `list_skills`
   - `load_skill`
   - `read_skill_file`
   - `run_skill_script`
4. 主 Agent 看到 catalog 后，如果当前任务需要某个 skill，可在同一轮工具循环里调用 `load_skill`。
5. 如 skill 指令要求读取资料或运行脚本，Agent 再调用 `read_skill_file` 或 `run_skill_script`。

## 安全边界

- 只能访问 `skills_loader/skills/<skill_id>/` 内部文件。
- 拒绝绝对路径和 `../` 路径逃逸。
- `run_skill_script` 只允许执行入口位于 skill 内 `scripts/` 的命令。LLM 可以传：
  - `["python", "scripts/tool.py", "--x", "1"]`
  - `["bash", "scripts/tool.sh", "--x", "1"]`
  - `["sh", "scripts/tool.sh", "--x", "1"]`
  - `["scripts/tool_binary", "--x", "1"]`
- 执行器不接受 shell 字符串，不通过 shell 解释命令；禁止 `python -c`、`python -m`、`sh -c`
  这类 inline/module/shell 执行模式。
- `command` 每一项都会拒绝常见 shell 注入元字符，例如 `;`、`|`、`&`、`` ` ``、`$`、`<`、
  `>` 和换行。复杂 JSON、URL query 或自由文本参数应通过 `stdin` 传入。
- `run_skill_script` 不理解业务参数。LLM 根据 `SKILL.md` 的说明选择传参方式：
  - `stdin`: 原始标准输入文本。
  - 如果 skill 要求 stdin JSON，由 LLM 把 JSON 序列化成字符串传给 `stdin`。
- 脚本输出由执行器统一捕获，返回 `exit_code`、`stdout`、`stderr` 和 `timed_out`。
- 脚本使用当前后端 Python 解释器执行，`cwd` 为 skill 根目录，默认超时 120 秒。
- 脚本子进程只继承白名单环境变量。为支持内置 `tushare-data`，会透传 `TUSHARE_API` 和 `TUSHARE_TOKEN`；
  不继承后端完整环境变量或 `LLM_API_KEY`、`TAVILY_API_KEY`、`SECRET_KEY` 等敏感配置。
- 外部 skill 不获得交易执行权限；下单仍只走现有 Portfolio Manager 工具。

### `run_skill_script` 推荐用法

```json
{
  "skill_id": "tushare-data",
  "command": ["python", "scripts/call_tushare.py"],
  "stdin": "{\"interface_name\":\"trade_cal\",\"params\":{\"exchange\":\"SSE\"}}"
}
```

推荐命令形态：

- `["python", "scripts/tool.py", "--flag", "value"]`
- `["bash", "scripts/tool.sh", "--flag", "value"]`
- `["sh", "scripts/tool.sh", "--flag", "value"]`
- `["scripts/tool_binary", "--flag", "value"]`

禁止命令形态：

- `["python", "-c", "...", "scripts/noop.py"]`
- `["python", "-m", "module", "scripts/noop.py"]`
- `["sh", "-c", "...", "scripts/noop.sh"]`
- `["bash", "-c", "...", "scripts/noop.sh"]`
- `["curl", "https://example.com", "scripts/noop"]`

复杂 JSON、URL query、自然语言文本、SQL 片段等内容都应走 `stdin`，不要塞进 `command`。

## skill.json

每个 skill 目录必须包含 `skill.json`，由安装或维护 skill 的用户填写必填字段：

```json
{
  "name": "tushare-data",
  "description": "Tushare data research workflows."
}
```

`name` 和 `description` 必填；当前实现只读取这两个字段。

## 接入范围

- 辩论分析：`BaseAgent` 的 system prompt 追加 catalog，工具列表追加 skills loader tools。
- 交互式选股：研究 Agent 追加 catalog 和 tools。
- 经验复盘：`review_debate_conclusion` 保留 memory tools，并追加 catalog 和 skills loader tools。

## 测试策略

- 单元测试覆盖 skill 扫描、manifest 校验、catalog 生成、文件读取、脚本执行和路径逃逸拒绝。
- 回归测试覆盖现有 LLM orchestration、交互式选股和经验复盘的工具绑定不被破坏。
