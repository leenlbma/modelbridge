"""Translate the Messages protocol using the tested LiteLLM Responses adapter."""
import asyncio
import codecs
import json
import logging
import os

from .config import ROUTE
from .providers import DEFAULT_CONNECTION

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_TELEMETRY", "False")
os.environ.setdefault("LITELLM_LOG", "ERROR")

import litellm
import httpx
from litellm.llms.anthropic.pass_through.adapters.transformation import AnthropicAdapter
from litellm.llms.anthropic.pass_through.responses_adapters.transformation import LiteLLMAnthropicToResponsesAPIAdapter
from litellm.llms.anthropic.pass_through.responses_adapters.streaming_iterator import AnthropicResponsesStreamWrapper
from litellm.llms.anthropic.pass_through.utils import normalize_reasoning_effort_value
from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import ModelResponse, ModelResponseStream

litellm.telemetry = False
litellm.suppress_debug_info = True
litellm.set_verbose = False
for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    logging.getLogger(name).setLevel(logging.CRITICAL)


class ConnectionFailure(Exception):
    def __init__(self, code, status=502):
        self.code, self.status = code, status
        super().__init__(code)


def describe(exc):
    status = getattr(exc, "status_code", getattr(exc, "status", 502))
    status = status if isinstance(status, int) and 400 <= status <= 599 else 502
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in {"model_unavailable", "invalid_reply", "network", "timeout"}:
        code = {401: "invalid_key", 403: "permission", 404: "model_unavailable", 429: "rate_limit"}.get(status, "upstream")
    if isinstance(exc, (httpx.ConnectError, httpx.ProxyError)):
        code = "network"
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        code = "timeout"
    messages = {
        "invalid_key": "Key 无效或已过期。请检查服务商、认证方式和 Key。",
        "permission": "这个 Key 没有所选模型或接口的权限。请检查服务商的授权设置。",
        "model_unavailable": "模型或接口不存在。请检查模型 ID、API Base URL 和接口类型。",
        "rate_limit": "请求过于频繁或额度不足。请稍后重试，并检查服务商额度。",
        "network": "无法连接模型服务。请检查服务地址、网络或 VPN。",
        "timeout": "模型服务响应超时。请检查网络后重试。",
        "invalid_reply": "模型返回内容不符合所选接口或工具调用要求。请检查接口类型，或换一个模型。",
        "upstream": "模型服务暂时无法完成请求。请稍后重试或导出诊断摘要。",
    }
    return status, code, messages[code]


def auth_headers(connection, key):
    headers = {"Authorization": "Bearer " + key} if connection["auth"] == "bearer" else {"x-api-key": key}
    if connection["protocol"] == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    return headers


async def model_list(key, connection=None):
    connection = connection or DEFAULT_CONNECTION
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get(connection["base_url"] + "/models", headers=auth_headers(connection, key))
        if response.status_code != 200:
            raise ConnectionFailure("upstream", response.status_code)
        try:
            return [item["id"] for item in response.json()["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)]
        except (ValueError, KeyError, TypeError):
            raise ConnectionFailure("invalid_reply") from None


async def create_message(model, key, params, connection=None):
    connection = connection or DEFAULT_CONNECTION
    adapter, mapping = AnthropicAdapter(), {}
    responses_adapter = LiteLLMAnthropicToResponsesAPIAdapter()
    body = {**params, "model": model}
    if connection["protocol"] == "responses":
        # Only use the pinned library's message translators. Its network dispatcher
        # guesses streaming support from model metadata, which is wrong for aliases.
        body = responses_adapter.translate_request(body)
        body.update(model=model, store=False, stream=bool(params.get("stream")))
        reasoning = body.get("reasoning")
        if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
            reasoning["effort"] = normalize_reasoning_effort_value(reasoning["effort"], model=model, custom_llm_provider="openai")
        endpoint = "/responses"
    elif connection["protocol"] == "chat_completions":
        body, mapping = adapter.translate_completion_input_params_with_tool_mapping(body.copy(), custom_llm_provider="openai")
        # Native Anthropic extensions must not leak into a compatible Chat API.
        supported = {"model", "messages", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stream", "stop", "tools", "tool_choice", "parallel_tool_calls", "response_format", "reasoning_effort", "user", "seed"}
        body = {k: v for k, v in body.items() if k in supported}
        # Send to this endpoint directly, never let model metadata reroute to Responses.
        endpoint = "/chat/completions"
    else:
        endpoint = "/messages"
    streaming = bool(params.get("stream"))
    client = httpx.AsyncClient(timeout=180, follow_redirects=False)
    response = None
    try:
        response = await client.send(client.build_request("POST", connection["base_url"] + endpoint, headers=auth_headers(connection, key), json=body), stream=streaming)
        if response.status_code != 200:
            raise ConnectionFailure("upstream", response.status_code)
        if not streaming:
            value = response.json()
            if endpoint == "/responses":
                if value.get("status") not in {"completed", "incomplete"}:
                    raise ConnectionFailure("invalid_reply")
                return responses_adapter.translate_response(ResponsesAPIResponse(**value))
            return adapter.translate_completion_output_params(ModelResponse(**value), tool_name_mapping=mapping) if endpoint == "/chat/completions" else value
    except BaseException:
        if response is not None:
            await response.aclose()
        await client.aclose()
        raise
    finally:
        if not streaming:
            await client.aclose()

    async def output():
        try:
            if endpoint == "/messages":
                async for chunk in response.aiter_bytes():
                    yield chunk
            else:
                async def chunks():
                    data = []
                    finished = False
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            raw = "\n".join(data)
                            data.clear()
                            if raw == "[DONE]":
                                if not finished:
                                    raise ConnectionFailure("invalid_reply")
                                return
                            value = json.loads(raw)
                            if "error" in value:
                                raise ConnectionFailure("upstream")
                            if endpoint == "/responses":
                                if value.get("type") in {"error", "response.failed"}:
                                    raise ConnectionFailure("upstream")
                                finished = finished or value.get("type") in {"response.completed", "response.incomplete"}
                                yield value
                            else:
                                finished = finished or any(c.get("finish_reason") for c in value.get("choices", []))
                                yield ModelResponseStream(**value)
                    if data or not finished:
                        raise ConnectionFailure("invalid_reply")
                translated = AnthropicResponsesStreamWrapper(chunks(), model=model).async_anthropic_sse_wrapper() if endpoint == "/responses" else adapter.translate_completion_output_params_streaming(chunks(), model=model, tool_name_mapping=mapping)
                async for chunk in translated:
                    yield chunk
        finally:
            await response.aclose()
            await client.aclose()
    return output()


def stamp(value):
    value = value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value
    if not isinstance(value, dict):
        raise ConnectionFailure("invalid_reply")
    if value.get("type") == "error":
        raise ConnectionFailure("upstream")
    if value.get("type") == "message":
        value["model"] = ROUTE
    if value.get("type") == "message_start":
        value["message"]["model"] = ROUTE
    return value


def validate_message(value, complete=True):
    """Validate the common Messages envelope before calling a request successful."""
    value = value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value
    if isinstance(value, dict) and value.get("type") == "error":
        raise ConnectionFailure("upstream")
    if not isinstance(value, dict) or value.get("type") != "message" or value.get("role") != "assistant":
        raise ConnectionFailure("invalid_reply")
    if any(not isinstance(value.get(k), str) or not value[k] for k in ("id", "model")):
        raise ConnectionFailure("invalid_reply")
    content, usage = value.get("content"), value.get("usage")
    if not isinstance(content, list) or not isinstance(usage, dict):
        raise ConnectionFailure("invalid_reply")
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
        raise ConnectionFailure("invalid_reply")
    if complete and (not isinstance(value.get("stop_reason"), str) or not value["stop_reason"]):
        raise ConnectionFailure("invalid_reply")
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str) or not block["type"]:
            raise ConnectionFailure("invalid_reply")
        if block["type"] == "text" and not isinstance(block.get("text"), str):
            raise ConnectionFailure("invalid_reply")
        if block["type"] == "tool_use" and (any(not isinstance(block.get(k), str) or not block[k] for k in ("id", "name")) or not isinstance(block.get("input"), dict)):
            raise ConnectionFailure("invalid_reply")
    return stamp(value)


async def stream_events(result):
    """Accept split UTF-8/SSE frames as well as SDK dictionary events."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending = ""
    started = False

    def checked(value):
        nonlocal started
        if isinstance(value, dict) and value.get("type") == "message_start":
            value["message"] = validate_message(value.get("message"), complete=False)
        value = stamp(value)
        kind = value.get("type")
        if not isinstance(kind, str) or not kind:
            raise ConnectionFailure("invalid_reply")
        if kind == "message_start":
            if started:
                raise ConnectionFailure("invalid_reply")
            started = True
        elif kind != "ping" and not started:
            raise ConnectionFailure("invalid_reply")
        return value

    try:
        async for chunk in result:
            if not isinstance(chunk, (bytes, str)):
                if pending.strip():
                    raise ConnectionFailure("invalid_reply")
                event = checked(chunk)
                yield event
                if event["type"] == "message_stop":
                    return
                continue
            pending += decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
            pending = pending.replace("\r\n", "\n")
            while "\n\n" in pending:
                frame, pending = pending.split("\n\n", 1)
                data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
                if data and data != "[DONE]":
                    event = checked(json.loads(data))
                    yield event
                    if event["type"] == "message_stop":
                        return
        pending += decoder.decode(b"", final=True)
        # EOF without message_stop is never a complete reply.
        raise ConnectionFailure("invalid_reply")
    finally:
        close = getattr(result, "aclose", None)
        if close:
            await close()


async def verify_model(key, model, connection=None):
    """Synthetic end-to-end validation of text, streamed tool arguments and results."""
    async def call(params):
        return await create_message(model, key, params, connection)
    reply = validate_message(await call({"messages": [{"role": "user", "content": "Reply with exactly OK."}], "max_tokens": 128}))
    if not any("OK" in block.get("text", "") for block in reply.get("content", [])):
        raise ConnectionFailure("invalid_reply")
    tool = {"name": "echo_value", "description": "Echo a synthetic test value. No external action.", "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}}
    messages = [{"role": "user", "content": "Call echo_value with value bridge-ready."}]
    result = await call({"messages": messages, "max_tokens": 512, "tools": [tool], "tool_choice": {"type": "tool", "name": "echo_value"}, "stream": True})
    blocks, arguments, finished = {}, {}, False
    async for event in stream_events(result):
        if event["type"] == "error":
            raise ConnectionFailure("invalid_reply")
        if event["type"] == "content_block_start":
            blocks[event["index"]] = event["content_block"]
        if event["type"] == "content_block_delta" and event["delta"]["type"] == "input_json_delta":
            i = event["index"]
            arguments[i] = arguments.get(i, "") + event["delta"]["partial_json"]
        if event["type"] == "message_stop":
            finished = True
    calls = []
    for i, block in blocks.items():
        if block.get("type") == "tool_use":
            block["input"] = json.loads(arguments[i]) if i in arguments else block.get("input", {})
            calls.append(block)
    if not finished or len(calls) != 1 or calls[0].get("name") != "echo_value" or calls[0]["input"] != {"value": "bridge-ready"}:
        raise ConnectionFailure("invalid_reply")
    messages += [{"role": "assistant", "content": calls}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": calls[0]["id"], "content": "bridge-ready"}]}, {"role": "user", "content": "Reply with the tool result only; do not call tools again."}]
    reply = validate_message(await call({"messages": messages, "tools": [tool], "max_tokens": 128}))
    if not any("bridge-ready" in block.get("text", "") for block in reply.get("content", [])):
        raise ConnectionFailure("invalid_reply")
    return ["文字回复", "流式工具调用", "工具结果回传"]
