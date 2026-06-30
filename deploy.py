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
FIRST_SUPERUSER={env_value(config["superuser"])}
FIRST_SUPERUSER_EMAIL={env_value(config["superuser_email"])}
FIRST_SUPERUSER_PASSWORD={env_value(config["superuser_password"])}
SECRET_KEY={env_value(config["secret_key"])}

LLM_API_KEY={env_value(config["litellm_master_key"])}
ENABLE_OPENAPI_DOCS=false
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
MEMOFLUX_LLM_MODEL=openai-compatible
"""


def render_litellm_config(config: dict[str, str]) -> str:
    """
    渲染 LiteLLM 配置文件内容。

    Args:
        config: 部署配置字典

    Returns:
        litellm/config.yaml 文件内容
    """
    master_key = config["litellm_master_key"]
    endpoints = json.loads(config["llm_endpoints"])
    shared_model_entries = []
    openai_compatible_entries = []
    thinking_entries = []
    for index, endpoint in enumerate(endpoints):
        anchor_name = f"shared_litellm_params{index}"
        shared_model_entries.append(
            f"""  - model_name: gpt-4o-mini
    litellm_params:
      <<: &{anchor_name}
        model: {yaml_string(endpoint['litellm_model'])}
        api_key: {yaml_string(endpoint['api_key'])}
        api_base: {yaml_string(endpoint['api_base'])}
      temperature: 0.2
""".rstrip()
        )
        openai_compatible_entries.append(
            f"""  - model_name: openai-compatible
    litellm_params:
      <<: *{anchor_name}
      temperature: 0.2
      extra_body:
        thinking:
          type: disabled"""
        )
        thinking_entries.append(
            f"""  - model_name: openai-compatible-thinking
    litellm_params:
      <<: *{anchor_name}
      extra_body:
        thinking:
          type: enabled"""
        )

    model_list = "\n\n".join([*shared_model_entries, *openai_compatible_entries, *thinking_entries])
    return f"""# Generated by deploy.py. Do not commit this file.
model_list:
{model_list}

general_settings:
  master_key: {yaml_string(master_key)}
  database_url: postgresql://tradeuser:tradepassword@postgres:5432/litellm
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


def collect_llm_endpoint(index: int) -> dict[str, str]:
    """
    采集并校验一组 LLM API 配置。

    Args:
        index: 当前采集的 LLM API 序号。

    Returns:
        已通过校验的 LLM API 配置。
    """
    endpoint: dict[str, str] = {}
    endpoint["api_base"] = prompt_text(f"LLM API #{index} Base URL", "https://api.deepseek.com")
    endpoint["validation_model"] = prompt_text(f"LLM API #{index} 供应商真实模型名（用于直连校验）", "deepseek-v4-flash")
    endpoint["litellm_model"] = prompt_text(
        f"LLM API #{index} LiteLLM provider/model 写法（例如 deepseek/deepseek-v4-flash）",
        "deepseek/deepseek-v4-flash",
    )
    while True:
        endpoint["api_key"] = prompt_text(f"LLM API #{index} Key", secret=True)
        ok, message = validate_llm(
            endpoint["api_base"],
            endpoint["api_key"],
            endpoint["validation_model"],
        )
        print(message)
        if ok:
            return endpoint
        print("校验失败，请重新输入该 LLM API 配置。")
        endpoint["api_base"] = prompt_text(f"LLM API #{index} Base URL", endpoint["api_base"])
        endpoint["validation_model"] = prompt_text(
            f"LLM API #{index} 供应商真实模型名（用于直连校验）",
            endpoint["validation_model"],
        )
        endpoint["litellm_model"] = prompt_text(
            f"LLM API #{index} LiteLLM provider/model 写法（例如 deepseek/deepseek-v4-flash）",
            endpoint["litellm_model"],
        )


def collect_llm_endpoints() -> list[dict[str, str]]:
    """
    采集一组或多组 LLM API 配置。

    Returns:
        LLM API 配置列表，至少包含一组。
    """
    endpoints = [collect_llm_endpoint(1)]
    while prompt_bool("是否继续添加另一组 LLM API Key 和 URL", False):
        endpoints.append(collect_llm_endpoint(len(endpoints) + 1))
    return endpoints


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

    config["llm_endpoints"] = json.dumps(collect_llm_endpoints(), ensure_ascii=False)
    print("Tushare、Tavily、NewsAPI 配置不在部署阶段采集。启动后请进入 UI 系统设置 > 数据源设置 填写。")
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
    ok, message = wait_for_litellm_gateway("http://localhost:4000/v1", litellm_master_key, "openai-compatible")
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
