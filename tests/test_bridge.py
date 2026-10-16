import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bridge.config import ROUTE, Store, desktop_config
from bridge.protocol import ConnectionFailure, stream_events
from bridge.server import create_app
from installer import plist_value

KEY = "unit-test-key-not-a-real-credential"


def message_reply(text="OK"):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "upstream", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}


class ConfigTests(unittest.TestCase):
    def test_profile_persists_only_digest_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            store.save(KEY, "gpt-example", "test-time")
            self.assertNotIn(KEY, store.path.read_text())
            self.assertTrue(Store(temp).accepts(KEY))
            self.assertFalse(Store(temp).accepts(KEY + "wrong"))
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_config_still_allows_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "profile.json").write_text("broken")
            store = Store(temp)
            self.assertTrue(store.load_error)
            self.assertIsNone(store.profile)

    def test_export_has_no_key_and_uses_selected_model(self):
        config = desktop_config(18782, "gpt-example")
        self.assertNotIn("inferenceGatewayApiKey", config)
        self.assertEqual(config["inferenceGatewayBaseUrl"], "http://127.0.0.1:18782")
        self.assertEqual(config["inferenceModels"][0]["name"], ROUTE)
        self.assertIn("GPT-EXAMPLE", config["inferenceModels"][0]["labelOverride"])
        self.assertFalse(config["modelPrefer1mContext"])

    def test_installer_paths_are_portable_and_do_not_contain_keys(self):
        value = plist_value(Path("/Users/test user/Bridge/release"), Path("/Users/test user/Bridge/data"), 18782, "id")
        self.assertEqual(value["ProgramArguments"][0], "/Users/test user/Bridge/release/ModelBridge 模型连接服务.app/Contents/MacOS/ModelBridge 模型连接服务")
        self.assertNotIn(KEY, json.dumps(value))
        self.assertTrue(value["RunAtLoad"])


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_split_unicode_and_multiple_frames(self):
        values = [{"type": "message_start", "message": {**message_reply(), "content": [], "stop_reason": None}}, {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}}, {"type": "message_stop"}]
        raw = "".join("event: " + v["type"] + "\r\ndata: " + json.dumps(v, ensure_ascii=False) + "\r\n\r\n" for v in values).encode()
        async def source():
            for i in range(0, len(raw), 3):
                yield raw[i:i + 3]
        result = [item async for item in stream_events(source())]
        self.assertEqual(result[0]["message"]["model"], ROUTE)
        self.assertEqual(result[1]["delta"]["text"], "你好")
        self.assertEqual(result[-1]["type"], "message_stop")

    async def test_incomplete_sse_is_failure(self):
        async def source():
            yield b'data: {"type":"message_stop"}'
        with self.assertRaises(ConnectionFailure):
            _ = [item async for item in stream_events(source())]


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name)
        self.store = self.app.state.store
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 5555)), base_url="http://127.0.0.1:18766")
        self.admin = {"Authorization": "Bearer " + self.store.admin}
        self.key_headers = {"Authorization": "Bearer " + KEY}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_separate_admin_and_inference_auth(self):
        self.store.save(KEY, "gpt-example", "test")
        self.assertEqual((await self.client.get("/api/status", headers=self.key_headers)).status_code, 401)
        self.assertEqual((await self.client.get("/v1/models", headers=self.admin)).status_code, 401)
        self.assertEqual((await self.client.get("/v1/models", headers=self.key_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/v1/models")).status_code, 401)

    async def test_browser_cross_origin_and_dns_rebinding_blocked(self):
        self.assertEqual((await self.client.get("/api/status", headers={**self.admin, "Origin": "https://other.example"})).status_code, 403)
        self.assertEqual((await self.client.get("/health", headers={"Host": "other.example"})).status_code, 403)

    async def test_setup_rejects_invalid_json(self):
        for body in [[], "hello", {"key": ""}, {"key": 42}]:
            self.assertEqual((await self.client.post("/api/models", headers=self.admin, json=body)).status_code, 400)

    async def test_connect_failure_preserves_working_profile_and_redacts_error(self):
        self.store.save(KEY, "gpt-old", "test")
        original = self.store.path.read_bytes()
        with patch("bridge.protocol.model_list", AsyncMock(return_value=["gpt-new"])), patch("bridge.protocol.verify_model", AsyncMock(side_effect=RuntimeError("private prompt " + KEY))):
            reply = await self.client.post("/api/connect", headers=self.admin, json={"key": KEY, "model": "gpt-new"})
        self.assertEqual(reply.status_code, 502)
        self.assertNotIn(KEY, reply.text)
        self.assertNotIn("private prompt", reply.text)
        self.assertEqual(self.store.path.read_bytes(), original)

    async def test_manual_model_verification_does_not_require_discovery(self):
        verify, discover = AsyncMock(return_value=["test"]), AsyncMock(side_effect=RuntimeError("no /models"))
        with patch("bridge.protocol.model_list", discover), patch("bridge.protocol.verify_model", verify):
            reply = await self.client.post("/api/connect", headers=self.admin, json={"key": KEY, "model": "vendor/manual-model"})
        self.assertEqual(reply.status_code, 200)
        discover.assert_not_called()
        verify.assert_awaited_once()
        self.assertEqual(self.store.profile["model"], "vendor/manual-model")

    async def test_successful_setup_and_diagnostics_without_credentials(self):
        with patch("bridge.protocol.model_list", AsyncMock(return_value=["gpt-new"])), patch("bridge.protocol.verify_model", AsyncMock(return_value=["test"])):
            reply = await self.client.post("/api/connect", headers=self.admin, json={"key": KEY, "model": "gpt-new"})
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(self.store.accepts(KEY))
        diagnostic = await self.client.get("/api/diagnostics", headers=self.admin)
        self.assertNotIn(KEY, diagnostic.text)
        self.assertNotIn(self.store.profile["key_sha256"], diagnostic.text)
        self.assertNotIn(self.temp.name, diagnostic.text)

    async def test_upstream_overrides_from_client_are_discarded(self):
        self.store.save(KEY, "gpt-new", "test")
        create = AsyncMock(return_value=message_reply())
        with patch("bridge.protocol.create_message", create):
            reply = await self.client.post("/v1/messages", headers=self.key_headers, json={"model": ROUTE, "messages": [{"role": "user", "content": "Hi"}], "api_base": "https://other.example", "api_key": "other"})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["model"], ROUTE)
        self.assertEqual(create.call_args.args[:2], ("gpt-new", KEY))
        self.assertNotIn("api_base", create.call_args.args[2])
        self.assertNotIn("api_key", create.call_args.args[2])

    async def test_count_tokens_non_object_returns_400(self):
        self.store.save(KEY, "gpt-new", "test")
        reply = await self.client.post("/v1/messages/count_tokens", headers=self.key_headers, json=[])
        self.assertEqual(reply.status_code, 400)

    async def test_stream_failure_is_sanitized_and_active_count_cleared(self):
        self.store.save(KEY, "gpt-new", "test")
        async def broken():
            yield {"type": "message_start", "message": {**message_reply(), "content": [], "stop_reason": None}}
            raise RuntimeError(KEY)
        with patch("bridge.protocol.create_message", AsyncMock(return_value=broken())):
            reply = await self.client.post("/v1/messages", headers=self.key_headers, json={"model": ROUTE, "messages": [{"role": "user", "content": "test"}], "stream": True})
        self.assertIn("event: error", reply.text)
        self.assertNotIn(KEY, reply.text)
        status = (await self.client.get("/api/status", headers=self.admin)).json()
        self.assertEqual(status["requests"]["active"], 0)
        self.assertEqual(status["requests"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
