#!/usr/bin/env python3
"""交互式生成部署配置并启动 Docker Compose。"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_ENV = ROOT_DIR / "backend" / ".env"
MEMO_ENV = ROOT_DIR / "memo" / ".env"
LITELLM_CONFIG = ROOT_DIR / "litellm" / "config.yaml"
HTTP_TIMEOUT_SECONDS = 30
LITELLM_GATEWAY_RETRY_SECONDS = 5
LITELLM_GATEWAY_RETRY_ATTEMPTS = 12


def prompt_text(label: str, default: str | None = None, secret: bool = False) -> str:
    """
    读取非空文本输入。

    Args:
        label: 展示给用户的输入项名称
        default: 用户直接回车时使用的默认值
        secret: 是否用隐藏输入读取敏感内容

    Returns:
        用户输入或默认值
    """
    suffix = f" [{default}]" if default else ""
    prompt = f"{label}{suffix}: "
    while True:
        value = getpass.getpass(prompt) if secret else input(prompt)
        value = value.strip()
        if value:
            return value
        if default is not None:
            return default
        print("该项不能为空，请重新输入。")


def prompt_password() -> str:
    """
    读取并确认初始超级用户密码。

    Returns:
        已确认的密码明文，用于写入本地部署配置
    """
    while True:
        password = prompt_text("初始超级用户密码", secret=True)
        if len(password) < 12:
            print("密码至少 12 位，请重新输入。")
            continue
        confirm = prompt_text("再次输入初始超级用户密码", secret=True)
        if password == confirm:
            return password
        print("两次密码不一致，请重新输入。")


def prompt_bool(label: str, default: bool) -> bool:
    """
    读取布尔选项。

    Args:
        label: 展示给用户的问题
        default: 用户直接回车时使用的默认值

    Returns:
        用户选择的布尔值
    """
    hint = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{hint}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def read_json_response(request: Request) -> tuple[bool, dict[str, Any] | str]:
    """
    发送 HTTP 请求并解析 JSON 响应。

    Args:
        request: 已构造的 urllib 请求

    Returns:
        二元组，第一项表示请求是否成功，第二项为 JSON 对象或错误说明
    """
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            if not body:
                return True, {}
            return True, json.loads(body)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        return False, f"HTTP {exc.code}: {detail}"
    except URLError as exc:
        return False, f"网络请求失败: {exc.reason}"
    except http.client.HTTPException as exc:
        return False, f"HTTP 连接失败: {exc}"
    except OSError as exc:
        return False, f"网络连接失败: {exc}"
    except TimeoutError:
        return False, "请求超时"
    except json.JSONDecodeError as exc:
        return False, f"响应不是合法 JSON: {exc}"


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[bool, Any]:
    """
    发送 JSON POST 请求。

    Args:
        url: 请求地址
        payload: JSON 请求体
        headers: 额外请求头

    Returns:
        请求成功状态和响应内容或错误说明
    """
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    return read_json_response(request)


def get_json(url: str, headers: dict[str, str] | None = None) -> tuple[bool, Any]:
    """
    发送 GET 请求并解析 JSON。

    Args:
        url: 请求地址
        headers: 额外请求头

    Returns:
        请求成功状态和响应内容或错误说明
    """
    request = Request(url, headers=headers or {}, method="GET")
    return read_json_response(request)


def validate_tushare(api_url: str, token: str) -> tuple[bool, str]:
    """
    验证 Tushare Token 是否可以读取低门槛日线数据。

    Args:
        api_url: Tushare dataapi 地址
        token: Tushare Token

    Returns:
        验证结果和说明
    """
    api_name = "daily"
    payload = {
        "api_name": api_name,
        "token": token,
        "params": {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240110"},
        "fields": "ts_code,trade_date,open,close",
    }
    ok, data = post_json(f"{api_url.rstrip('/')}/{api_name}", payload)
    if not ok:
        return False, str(data)
    if not isinstance(data, dict):
        return False, "响应格式不是 JSON 对象"
    if data.get("code") != 0:
        message = str(data.get("msg") or data)
        if "频率超限" in message or "rate limit" in message.lower():
            return True, f"Tushare Token 已被服务端接受，但当前触发频率限制: {message}"
        return False, message
    items = data.get("data", {}).get("items", [])
    if not items:
        return False, "接口返回成功但没有日线数据，请确认 Token 权限"
    return True, "Tushare 校验通过"


def validate_tavily(api_key: str) -> tuple[bool, str]:
    """
    验证 Tavily API Key 是否可执行搜索。

    Args:
        api_key: Tavily API Key

    Returns:
        验证结果和说明
    """
    payload = {"api_key": api_key, "query": "A股", "max_results": 1}
    ok, data = post_json("https://api.tavily.com/search", payload)
    if not ok:
        return False, str(data)
    if not isinstance(data, dict) or "results" not in data:
        return False, f"响应缺少 results 字段: {data}"
    return True, "Tavily 校验通过"


def validate_newsapi(api_key: str) -> tuple[bool, str]:
    """
    验证 NewsAPI API Key 是否可读取新闻。

    Args:
        api_key: NewsAPI API Key

    Returns:
        验证结果和说明
    """
    query = urlencode({"q": "stock", "pageSize": 1, "apiKey": api_key})
    ok, data = get_json(f"https://newsapi.org/v2/everything?{query}")
    if not ok:
        return False, str(data)
    if not isinstance(data, dict):
        return False, "响应格式不是 JSON 对象"
    if data.get("status") != "ok":
        return False, str(data.get("message") or data)
    return True, "NewsAPI 校验通过"


def chat_completions_url(api_base: str) -> str:
    """
    根据用户输入的 OpenAI-compatible base URL 生成聊天补全地址。

    Args:
        api_base: 用户输入的供应商 API base URL

    Returns:
        chat/completions 请求地址
    """
    normalized = api_base.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def validate_llm(api_base: str, api_key: str, model: str) -> tuple[bool, str]:
    """
    验证 OpenAI-compatible LLM Key 是否可调用指定模型。

    Args:
        api_base: 供应商 API base URL
        api_key: 供应商 API Key
        model: 用于直接校验的模型名

    Returns:
        验证结果和说明
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    ok, data = post_json(chat_completions_url(api_base), payload, headers)
    if not ok:
        return False, str(data)
    if not isinstance(data, dict) or "choices" not in data:
        return False, f"响应缺少 choices 字段: {data}"
    return True, "LLM 校验通过"


def wait_for_litellm_gateway(api_base: str, api_key: str, model: str) -> tuple[bool, str]:
    """
    等待 LiteLLM 网关接受健康检查和模型请求。

    Args:
        api_base: LiteLLM OpenAI-compatible API base URL
        api_key: LiteLLM master key
        model: LiteLLM 模型别名

    Returns:
        网关最终可用状态和最后一次校验说明
    """
    health_url = f"{api_base.rstrip('/').removesuffix('/v1')}/health/liveliness"
    last_message = ""
    for attempt in range(1, LITELLM_GATEWAY_RETRY_ATTEMPTS + 1):
        health_ok, health_message = get_json(health_url)
        if health_ok:
            model_ok, model_message = validate_llm(api_base, api_key, model)
            if model_ok:
                return True, model_message
            last_message = model_message
        else:
            last_message = str(health_message)
        print(
            f"LiteLLM 网关未就绪，"
            f"{LITELLM_GATEWAY_RETRY_SECONDS} 秒后重试 "
            f"({attempt}/{LITELLM_GATEWAY_RETRY_ATTEMPTS}): {last_message[:160]}"
        )
        time.sleep(LITELLM_GATEWAY_RETRY_SECONDS)
    return False, last_message


def collect_validated_secret(label: str, validator: Any) -> str:
    """
    循环读取并校验单个密钥。

    Args:
        label: 密钥名称
        validator: 接收密钥并返回验证结果的函数

    Returns:
        通过验证的密钥
    """
    while True:
        value = prompt_text(label, secret=True)
        ok, message = validator(value)
        print(message)
        if ok:
            return value
        print("校验失败，请重新输入。")


def env_value(value: str) -> str:
    """
    将字符串转成 dotenv 可读取的安全字面量。

    Args:
        value: 原始字符串

    Returns:
        dotenv 字符串字面量
    """
    return json.dumps(value, ensure_ascii=False)


def yaml_string(value: str) -> str:
    """
    将字符串转成 YAML 可读取的安全字面量。

    Args:
        value: 原始字符串

    Returns:
        YAML 字符串字面量
    """
    return json.dumps(value, ensure_ascii=False)


def render_backend_env(config: dict[str, str]) -> str:
    """
    渲染后端环境变量文件内容。

    Args:
        config: 部署配置字典

    Returns:
        backend/.env 文件内容
    """
    return f"""# Generated by deploy.py. Do not commit this file.
PROJECT_NAME=天枢智投
PROJECT_VERSION=v1.0.0

FIRST_SUPERUSER={env_value(config["superuser"])}
FIRST_SUPERUSER_EMAIL={env_value(config["superuser_email"])}
FIRST_SUPERUSER_PASSWORD={env_value(config["superuser_password"])}
SECRET_KEY={env_value(config["secret_key"])}

DATABASE_URL=postgresql://tradeuser:tradepassword@postgres:5432/trading
REDIS_URL=redis://redis:6379

LLM_PROVIDER=litellm
LLM_MODEL=backend
LLM_THINKING_MODEL=backend-thinking
LLM_API_KEY={env_value(config["litellm_master_key"])}
LLM_BASE_URL=http://litellm:4000/v1
LLM_TIMEOUT_SECONDS=240
LLM_MAX_RETRIES=3
RESEARCH_LLM_API_KEY=
DEBATE_AGENT_PARALLEL_ENABLED=true

ENABLE_AUTO_TRADE=true
ENABLE_RUNTIME_EXTENSIONS=false
ENABLE_OPENAPI_DOCS=false
BACKEND_CORS_ORIGINS=[]

ASYNC_TASK_MAX_CONCURRENT=8

PY_SANDBOX_ENABLED=true
PY_SANDBOX_BASE_URL=http://sandbox:8030
PY_SANDBOX_HTTP_TIMEOUT_SECONDS=35
PY_SANDBOX_TIMEOUT_SECONDS=30
PY_SANDBOX_STDOUT_MAX_BYTES=32768
PY_SANDBOX_STDERR_MAX_BYTES=16384

WEBFETCH_BASE_URL=http://webfetch:8010
WEBFETCH_TIMEOUT_SECONDS=180

TUSHARE_API={env_value(config["tushare_api"])}
TUSHARE_TOKEN={env_value(config["tushare_token"])}
DEFAULT_DATA_SOURCE=tushare
ENABLE_DATA_SOURCE_FAILOVER=true

SYSTEM_LANGUAGE=zh
TAVILY_API_KEY={env_value(config["tavily_api_key"])}
NEWS_API_KEY={env_value(config["news_api_key"])}
"""


def render_memo_env(master_key: str) -> str:
    """
    渲染 MemoFlux 环境变量文件内容。

    Args:
        master_key: LiteLLM 网关 master key

    Returns:
        memo/.env 文件内容
    """
    return f"""# Generated by deploy.py. Do not commit this file.
MEMOFLUX_LLM_BASE_URL=http://litellm:4000/v1
MEMOFLUX_LLM_API_KEY={master_key}
MEMOFLUX_LLM_MODEL=memory
"""


def render_litellm_config(config: dict[str, str]) -> str:
    """
    渲染 LiteLLM 配置文件内容。

    Args:
        config: 部署配置字典

    Returns:
        litellm/config.yaml 文件内容
    """
    model = config["litellm_model"]
    api_key = config["llm_api_key"]
    api_base = config["llm_api_base"]
    master_key = config["litellm_master_key"]
    return f"""# Generated by deploy.py. Do not commit this file.
model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: {yaml_string(model)}
      api_key: {yaml_string(api_key)}
      api_base: {yaml_string(api_base)}
      temperature: 0.2
      extra_body:
        thinking:
          type: disabled

  - model_name: backend
    litellm_params:
      model: {yaml_string(model)}
      api_key: {yaml_string(api_key)}
      api_base: {yaml_string(api_base)}
      temperature: 0.2
      extra_body:
        thinking:
          type: disabled

  - model_name: backend-thinking
    litellm_params:
      model: {yaml_string(model)}
      api_key: {yaml_string(api_key)}
      api_base: {yaml_string(api_base)}
      reasoning_effort: high
      extra_body:
        thinking:
          type: enabled

  - model_name: memory
    litellm_params:
      model: {yaml_string(model)}
      api_key: {yaml_string(api_key)}
      api_base: {yaml_string(api_base)}
      temperature: 0.1
      extra_body:
        thinking:
          type: disabled

general_settings:
  master_key: {yaml_string(master_key)}
  database_url: postgresql://tradeuser:tradepassword@postgres:5432/litellm

environment_variables:
  OPENAI_API_KEY: {yaml_string(master_key)}
  OPENAI_API_BASE: http://127.0.0.1:4000/v1
"""


def write_config_file(path: Path, content: str, overwrite: bool) -> None:
    """
    写入配置文件，并在需要时保护已有文件。

    Args:
        path: 目标文件路径
        content: 要写入的文件内容
        overwrite: 是否覆盖已有文件

    Raises:
        FileExistsError: 目标文件存在且未允许覆盖
    """
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path.relative_to(ROOT_DIR)} 已存在，未覆盖。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_command(command: list[str]) -> None:
    """
    执行外部命令并在失败时退出。

    Args:
        command: 命令及参数列表

    Raises:
        SystemExit: 命令返回非零状态码
    """
    print(f"$ {' '.join(command)}")
    result = subprocess.run(command, cwd=ROOT_DIR, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def collect_config() -> dict[str, str]:
    """
    交互式采集并校验部署配置。

    Returns:
        已通过校验的部署配置字典
    """
    config: dict[str, str] = {}
    config["superuser"] = prompt_text("初始超级用户名", "tradeuser")
    config["superuser_email"] = prompt_text("初始超级用户邮箱", "tradeuser@example.com")
    config["superuser_password"] = prompt_password()
    config["secret_key"] = secrets.token_urlsafe(48)
    config["litellm_master_key"] = f"sk-{secrets.token_urlsafe(32)}"

    config["llm_api_base"] = prompt_text("LLM API Base URL", "https://api.deepseek.com")
    config["llm_validation_model"] = prompt_text("供应商真实模型名（用于直连校验）", "deepseek-v4-flash")
    config["litellm_model"] = prompt_text("LiteLLM provider/model 写法（例如 deepseek/deepseek-v4-flash）", "deepseek/deepseek-v4-flash")
    while True:
        config["llm_api_key"] = prompt_text("LLM API Key", secret=True)
        ok, message = validate_llm(
            config["llm_api_base"],
            config["llm_api_key"],
            config["llm_validation_model"],
        )
        print(message)
        if ok:
            break
        print("校验失败，请重新输入 LLM API 配置。")
        config["llm_api_base"] = prompt_text("LLM API Base URL", config["llm_api_base"])
        config["llm_validation_model"] = prompt_text("供应商真实模型名（用于直连校验）", config["llm_validation_model"])
        config["litellm_model"] = prompt_text(
            "LiteLLM provider/model 写法（例如 deepseek/deepseek-v4-flash）",
            config["litellm_model"],
        )

    config["tushare_api"] = prompt_text("Tushare API URL", "http://api.waditu.com/dataapi")
    while True:
        config["tushare_token"] = prompt_text("Tushare Token", secret=True)
        ok, message = validate_tushare(config["tushare_api"], config["tushare_token"])
        print(message)
        if ok:
            break
        print("校验失败，请重新输入 Tushare 配置。")
        config["tushare_api"] = prompt_text("Tushare API URL", config["tushare_api"])

    config["tavily_api_key"] = collect_validated_secret("Tavily API Key", validate_tavily)
    config["news_api_key"] = collect_validated_secret("NewsAPI API Key", validate_newsapi)
    return config


def ensure_docker_available() -> None:
    """
    确认 Docker CLI 可用。

    Raises:
        SystemExit: Docker CLI 不存在
    """
    if shutil.which("docker") is None:
        raise SystemExit("未找到 docker 命令，请先安装 Docker Engine 和 Docker Compose v2。")
    run_command(["docker", "version"])
    run_command(["docker", "compose", "version"])


def start_stack() -> None:
    """
    拉取镜像、启动 Compose 服务并打印服务状态。
    """
    run_command(["docker", "compose", "pull"])
    run_command(["docker", "compose", "up", "-d"])
    run_command(["docker", "compose", "ps"])


def health_check(litellm_master_key: str) -> None:
    """
    执行容器内健康检查命令。

    Args:
        litellm_master_key: 部署脚本生成的 LiteLLM 网关 master key

    Raises:
        SystemExit: LiteLLM 网关模型别名校验失败
    """
    checks = [
        ["docker", "compose", "exec", "-T", "backend", "curl", "-f", "http://127.0.0.1:8000/health"],
        ["docker", "compose", "exec", "-T", "sandbox", "curl", "-f", "http://127.0.0.1:8030/health"],
        ["docker", "compose", "exec", "-T", "webfetch", "curl", "-f", "http://127.0.0.1:8010/health"],
        ["docker", "compose", "exec", "-T", "memo", "curl", "-f", "http://127.0.0.1:8020/v1/health"],
    ]
    for command in checks:
        run_command(command)
    ok, message = wait_for_litellm_gateway("http://localhost:4000/v1", litellm_master_key, "backend")
    print(message)
    if not ok:
        raise SystemExit("LiteLLM 网关校验失败，请检查 litellm/config.yaml 中的模型名和供应商配置。")


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    Returns:
        命令行参数命名空间
    """
    parser = argparse.ArgumentParser(description="生成部署配置并启动 Best-AI-Trader Docker Compose。")
    parser.add_argument("--no-start", action="store_true", help="只生成配置，不启动容器。")
    parser.add_argument("--no-health-check", action="store_true", help="启动后不执行健康检查。")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的本地配置文件。")
    return parser.parse_args()


def main() -> None:
    """
    执行交互式部署流程。
    """
    args = parse_args()
    print("Best-AI-Trader 交互式部署脚本")
    overwrite = args.overwrite
    existing_files = [path for path in [BACKEND_ENV, MEMO_ENV, LITELLM_CONFIG] if path.exists()]
    if existing_files and not overwrite:
        names = ", ".join(str(path.relative_to(ROOT_DIR)) for path in existing_files)
        overwrite = prompt_bool(f"以下配置已存在：{names}。是否覆盖", False)
        if not overwrite:
            raise SystemExit("用户选择不覆盖已有配置，部署已停止。")
    if not args.no_start:
        ensure_docker_available()
    config = collect_config()
    write_config_file(BACKEND_ENV, render_backend_env(config), overwrite)
    write_config_file(MEMO_ENV, render_memo_env(config["litellm_master_key"]), overwrite)
    write_config_file(LITELLM_CONFIG, render_litellm_config(config), overwrite)
    print("配置已生成：backend/.env, memo/.env, litellm/config.yaml")

    if args.no_start:
        return
    start_stack()
    if not args.no_health_check:
        health_check(config["litellm_master_key"])
    print("部署完成。访问 http://localhost 打开主系统。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\n用户取消部署。")
