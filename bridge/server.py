import argparse
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import tiktoken

from . import __version__
from .config import DEFAULT_MODEL, ROUTE, Store, desktop_config, display_name, valid_model
from .providers import PRESETS, DEFAULT_CONNECTION, normalize_connection
from . import protocol

WEB = Path(__file__).resolve().parent.parent / "web"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_app(data_dir, port=18766, install_id="development", verification_timeout=180):
    store = Store(data_dir)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(store.root / "tokenizers")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    stats = {"completed": 0, "failed": 0, "active": 0, "last_success": None, "last_error": None}
    verification_lock = asyncio.Lock()
    verification_task = None
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def error(status, message, code="invalid_request", anthropic=False):
        value = {"code": code, "message": message}
        if anthropic:
            value = {"type": "error", "error": {"type": "api_error", "message": message}}
        return JSONResponse(value, status_code=status)

    def key_of(request):
        bearer = request.headers.get("authorization", "")
        return bearer[7:] if bearer.lower().startswith("bearer ") else request.headers.get("x-api-key", "")

    def failure(exc, anthropic=False):
        status, code, message = protocol.describe(exc)
        stats["last_error"] = {"at": now(), "code": code, "http_status": status}
        return error(status, message, code, anthropic)

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.client and request.client.host not in ("127.0.0.1", "::1"):
            return error(403, "仅允许本机连接。")
        if request.headers.get("host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return error(403, "请通过本机安装入口打开。")
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return error(403, "不接受其他网站发起的请求。")
        path = request.url.path
        if path.startswith("/api/") and not secrets.compare_digest(key_of(request), store.admin):
            return error(401, "请重新双击“打开 ModelBridge”进入控制台。", "console_auth")
        if path.startswith("/v1/") and not store.accepts(key_of(request)):
            return error(401, "请在 Claude 中填写与 Bridge 验证时相同的服务商 Key。", anthropic=True)
        if request.method == "POST":
            if not request.headers.get("content-type", "").startswith("application/json"):
                return error(415, "请求必须为 JSON。")
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})
        return response

    async def body_of(request, limit=32 * 1024 * 1024):
        buffer = bytearray()
        async for chunk in request.stream():
            buffer.extend(chunk)
            if len(buffer) > limit:
                raise ValueError("too large")
        value = json.loads(buffer)
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value

    async def setup_body(request):
        body = await body_of(request, limit=8192)
        key = body.get("key")
        if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(c.isspace() for c in key):
            raise ValueError("invalid key")
        return body, key

    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")

    @app.get("/app.js")
    async def javascript():
        return FileResponse(WEB / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    async def stylesheet():
        return FileResponse(WEB / "style.css", media_type="text/css")

    @app.get("/icon.svg")
    async def app_icon():
        return FileResponse(WEB / "icon.svg", media_type="image/svg+xml")

    @app.get("/health")
    async def health():
        return {"product": "ModelBridge", "version": __version__, "status": "ok", "configured": bool(store.profile), "install_id": install_id}

    def status_value():
        profile = store.profile or {}
        return {"product": "ModelBridge", "version": __version__, "configured": bool(store.profile), "config_error": store.load_error, "model": profile.get("model"), "connection": profile.get("connection"), "display_name": display_name(profile["model"], profile["connection"]) if profile else None, "verified_at": profile.get("verified_at"), "gateway_url": f"http://127.0.0.1:{port}", "route": ROUTE, "claude_installed": Path("/Applications/Claude.app").exists(), "requests": stats.copy(), "verifying": verification_lock.locked(), "default_model": DEFAULT_MODEL, "presets": PRESETS}

    @app.get("/api/status")
    async def status():
        return status_value()

    @app.get("/api/diagnostics")
    async def diagnostics():
        value = status_value()
        value.pop("presets")
        if value["connection"]:
            # Custom URL paths and user-chosen names can contain private data.
            value["connection"] = {k: value["connection"][k] for k in ("id", "protocol", "auth")}
        value.pop("display_name")
        return {"generated_at": now(), **value, "privacy": "不含 Key、指纹、对话内容、服务地址、连接名称或用户路径。"}

    @app.post("/api/models")
    async def models_probe(request: Request):
        try:
            body, key = await setup_body(request)
            connection = normalize_connection(body.get("connection", DEFAULT_CONNECTION))
        except (ValueError, UnicodeDecodeError):
            return error(400, "请检查服务地址、接口类型和 Key；远程地址需使用 HTTPS。")
        try:
            models = sorted(set(m for m in await protocol.model_list(key, connection) if valid_model(m)))
            if not models:
                return error(404, "服务没有返回可用模型。可以直接填写模型 ID，再验证连接。", "no_models")
            if DEFAULT_MODEL in models:
                models.remove(DEFAULT_MODEL)
                models.insert(0, DEFAULT_MODEL)
            return {"models": models}
        except Exception as exc:
            return failure(exc)

    @app.post("/api/connect")
    async def connect(request: Request):
        nonlocal verification_task
        try:
            body, key = await setup_body(request)
            model = body.get("model")
            connection = normalize_connection(body.get("connection", DEFAULT_CONNECTION))
            if not valid_model(model):
                raise ValueError("invalid model")
        except (ValueError, UnicodeDecodeError):
            return error(400, "请填写 Key、有效的模型 ID 和服务地址，并检查接口与认证方式。")
        if verification_lock.locked():
            return error(409, "已有验证正在进行，请等待完成。", "busy")
        async with verification_lock:
            start = time.monotonic()
            try:
                verification_task = asyncio.create_task(protocol.verify_model(key, model, connection))
                async with asyncio.timeout(verification_timeout):
                    checks = await verification_task
                # Existing inference keeps its own connection throughout verification.
                # Commit without yielding, only after all existing requests finish.
                if stats["active"]:
                    return error(409, "验证通过，但 Claude 仍有未完成请求，尚未切换。请等回复完成后重试。", "busy")
                store.save(key, model, now(), connection)
                # Counts describe only the active connection, not a previous provider.
                stats.update(completed=0, failed=0, last_success=None, last_error=None)
                return {"ok": True, "checks": checks, "duration_ms": round((time.monotonic() - start) * 1000), **status_value()}
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                return error(409, "验证已取消。", "cancelled")
            except TimeoutError:
                return failure(protocol.ConnectionFailure("timeout", 504))
            except Exception as exc:
                return failure(exc)
            finally:
                verification_task = None

    @app.post("/api/connect/cancel")
    async def cancel_verification():
        if verification_task is None or verification_task.done():
            return {"cancelled": False}
        verification_task.cancel()
        return {"cancelled": True}

    @app.get("/api/claude-config")
    async def config():
        if not store.profile:
            return error(409, "先完成模型验证，再生成配置。")
        return desktop_config(port, store.profile["model"], store.profile["connection"])

    @app.get("/v1/models")
    async def models():
        return {"data": [{"id": ROUTE, "type": "model", "display_name": display_name(store.profile["model"], store.profile["connection"]), "created_at": "2026-10-16T00:00:00Z"}], "has_more": False, "first_id": ROUTE, "last_id": ROUTE}

    @app.post("/v1/messages")
    async def messages(request: Request):
        try:
            body = await body_of(request)
        except (ValueError, UnicodeDecodeError):
            return error(400, "无效或过大的 JSON 请求。", anthropic=True)
        if body.get("model") != ROUTE:
            return error(404, "当前仅配置了路由 " + ROUTE, anthropic=True)
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            return error(400, "messages 不能为空。", anthropic=True)
        # Reading a request body can yield while another request changes provider.
        # Recheck against the exact profile we are about to forward to.
        if not store.accepts(key_of(request)):
            return error(401, "连接已切换，请更新 Claude 中的服务商 Key。", anthropic=True)
        allowed = ("messages", "max_tokens", "stream", "system", "metadata", "tools", "tool_choice", "thinking", "temperature", "top_p", "top_k", "stop_sequences", "output_config", "output_format", "context_management")
        params = {k: body[k] for k in allowed if k in body}
        params.setdefault("max_tokens", 4096)
        profile = store.profile.copy()
        stats["active"] += 1
        try:
            result = await protocol.create_message(profile["model"], key_of(request), params, profile["connection"])
            if not body.get("stream"):
                response = JSONResponse(protocol.validate_message(result))
                stats["completed"] += 1
                stats["last_success"] = now()
                stats["active"] -= 1
                return response
        except BaseException as exc:
            stats["active"] -= 1
            if not isinstance(exc, Exception):
                raise
            stats["failed"] += 1
            return failure(exc, anthropic=True)

        async def events():
            iterator = protocol.stream_events(result).__aiter__()
            pending = None
            finished = False
            try:
                while True:
                    if pending is None:
                        pending = asyncio.create_task(anext(iterator))
                    done, _ = await asyncio.wait({pending}, timeout=15)
                    if not done:
                        yield 'event: ping\ndata: {"type":"ping"}\n\n'
                        continue
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        break
                    pending = None
                    if event.get("type") == "error":
                        raise protocol.ConnectionFailure("upstream")
                    if event.get("type") == "message_stop":
                        finished = True
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if not finished:
                    raise protocol.ConnectionFailure("invalid_reply")
                stats["completed"] += 1
                stats["last_success"] = now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status_code, code, message = protocol.describe(exc)
                stats["failed"] += 1
                stats["last_error"] = {"at": now(), "code": code, "http_status": status_code}
                yield "event: error\ndata: " + json.dumps({"type": "error", "error": {"type": "api_error", "message": message}}, ensure_ascii=False) + "\n\n"
            finally:
                stats["active"] -= 1
                if pending and not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
                await iterator.aclose()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        try:
            body = await body_of(request)
            if body.get("model") != ROUTE:
                return error(404, "未知模型路由。", anthropic=True)
            content = {k: body[k] for k in ("system", "messages", "tools") if k in body}
            encoding = await asyncio.to_thread(tiktoken.get_encoding, "o200k_base")
            count = len(encoding.encode(json.dumps(content, ensure_ascii=False), disallowed_special=()))
            return {"input_tokens": count}
        except (ValueError, UnicodeDecodeError):
            return error(400, "无效或过大的 JSON 请求。", anthropic=True)
        except Exception as exc:
            return failure(exc, anthropic=True)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--install-id", default="development")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.data_dir, args.port, args.install_id), host="127.0.0.1", port=args.port, access_log=False, log_level="critical", proxy_headers=False)


if __name__ == "__main__":
    main()
