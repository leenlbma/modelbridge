"""Explicit API dialects and editable presets; never infer protocol from a model ID."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

PROTOCOLS = {"responses", "chat_completions", "anthropic"}
PRESETS = [
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "protocol": "responses", "auth": "bearer", "model": "", "note": "使用自己的 OpenAI API Key。模型支持情况以连接验证为准。"},
    {"id": "anthropic", "name": "Anthropic", "base_url": "https://api.anthropic.com/v1", "protocol": "anthropic", "auth": "x-api-key", "model": "", "note": "使用 Anthropic API Key，直接转发 Messages 请求。"},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "protocol": "chat_completions", "auth": "bearer", "model": "deepseek-chat", "note": "使用 DeepSeek API Key。模型需支持流式工具调用。"},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "protocol": "chat_completions", "auth": "bearer", "model": "", "note": "模型 ID 保留供应商前缀，例如供应商/模型名。"},
    {"id": "custom", "name": "自定义服务", "base_url": "", "protocol": "chat_completions", "auth": "bearer", "model": "", "note": "填写服务商给出的 API Base URL，再选择对应接口。支持本机 HTTP 服务。"},
]
DEFAULT_CONNECTION = {k: PRESETS[0][k] for k in ("id", "name", "base_url", "protocol", "auth")}


def valid_model(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}", value))


def normalize_connection(value):
    if not isinstance(value, dict):
        raise ValueError("请填写服务地址和接口类型。")
    name = value.get("name", "自定义服务")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
        raise ValueError("连接名称应为 1–60 个可见字符。")
    raw = value.get("base_url")
    if not isinstance(raw, str) or not raw or len(raw) > 2048 or re.search(r"[\s\\]", raw):
        raise ValueError("请填写完整的 API Base URL。")
    try:
        url = urlsplit(raw)
        host, port = url.hostname, url.port
    except ValueError:
        raise ValueError("API Base URL 格式不正确。") from None
    if not host or url.username is not None or url.password is not None or url.query or url.fragment:
        raise ValueError("地址不能包含账号、Key、查询参数或锚点。请将 Key 填入独立输入框。")
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    if url.scheme != "https" and not (url.scheme == "http" and local):
        raise ValueError("远程服务请使用 HTTPS；只有本机服务可以使用 HTTP。")
    path = url.path.rstrip("/")
    if any(path.endswith(s) for s in ("/responses", "/chat/completions", "/messages", "/models")):
        raise ValueError("请填写 API Base URL，去掉末尾的 /responses、/chat/completions、/messages 或 /models。")
    protocol, auth = value.get("protocol"), value.get("auth", "bearer")
    if protocol not in PROTOCOLS:
        raise ValueError("请选择支持的接口类型。")
    if auth not in {"bearer", "x-api-key"} or (auth == "x-api-key" and protocol != "anthropic"):
        raise ValueError("OpenAI 兼容接口使用 Bearer；Messages 接口可选 Bearer 或 x-api-key。")
    preset = value.get("id", "custom")
    if preset not in {p["id"] for p in PRESETS}:
        preset = "custom"
    return {"id": preset, "name": name.strip(), "base_url": urlunsplit((url.scheme, url.netloc, path, "", "")), "protocol": protocol, "auth": auth}
