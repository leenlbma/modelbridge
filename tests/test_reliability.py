"""Regression tests for outages, misleading success and failed service shutdown."""
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from bridge.config import ROUTE
from bridge.protocol import ConnectionFailure, stream_events
from bridge.server import create_app
import installer

KEY = 'reliability-fixture-credential'
NATIVE = {'id': 'custom', 'name': 'Fixture', 'base_url': 'https://fixture.example/v1', 'protocol': 'anthropic', 'auth': 'x-api-key'}
BODY = {'model': ROUTE, 'messages': [{'role': 'user', 'content': 'test'}], 'max_tokens': 16}


def reply():
    return {'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'old-model', 'content': [{'type': 'text', 'text': 'OK'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1}}


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, verification_timeout=.25)
        self.store = self.app.state.store
        self.store.save(KEY, 'old-model', 'test', NATIVE)
        self.original = self.store.path.read_bytes()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://127.0.0.1:18766')
        self.admin = {'Authorization': 'Bearer ' + self.store.admin}
        self.headers = {'x-api-key': KEY}
        self.candidate = {'key': 'new-fixture-credential', 'model': 'new-model', 'connection': NATIVE}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def stats(self):
        return (await self.client.get('/api/status', headers=self.admin)).json()['requests']

    def transport(self, handler):
        original = httpx.AsyncClient
        return patch('bridge.protocol.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    async def test_heartbeat_verification_times_out_without_blocking_existing_inference(self):
        ready, closed = asyncio.Event(), asyncio.Event()
        class Pings(httpx.AsyncByteStream):
            async def __aiter__(self):
                while True:
                    ready.set()
                    yield b'data: {"type":"ping"}\n\n'
                    await asyncio.sleep(.01)
            async def aclose(self):
                closed.set()
        def handler(request):
            body = json.loads(request.content)
            if body.get('stream'):
                return httpx.Response(200, stream=Pings(), headers={'content-type': 'text/event-stream'})
            return httpx.Response(200, json=reply())
        with self.transport(handler):
            task = asyncio.create_task(self.client.post('/api/connect', headers=self.admin, json=self.candidate))
            await asyncio.wait_for(ready.wait(), 2)
            response = await self.client.post('/v1/messages', headers=self.headers, json=BODY)
            self.assertEqual(response.status_code, 200)
            timeout = await asyncio.wait_for(task, 2)
        self.assertEqual(timeout.status_code, 504)
        self.assertEqual(timeout.json()['code'], 'timeout')
        self.assertTrue(closed.is_set())
        self.assertEqual(self.store.path.read_bytes(), self.original)
        self.assertEqual((await self.stats())['completed'], 1)
        with patch('bridge.protocol.verify_model', AsyncMock(return_value=['ok'])):
            self.assertEqual((await self.client.post('/api/connect', headers=self.admin, json=self.candidate)).status_code, 200)

    async def test_cancel_requires_admin_preserves_profile_and_releases_lock(self):
        ready, closed = asyncio.Event(), asyncio.Event()
        async def verify(*args):
            ready.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()
        with patch('bridge.protocol.verify_model', verify):
            task = asyncio.create_task(self.client.post('/api/connect', headers=self.admin, json=self.candidate))
            await asyncio.wait_for(ready.wait(), 2)
            blocked = await self.client.post('/api/connect', headers=self.admin, json=self.candidate)
            self.assertEqual(blocked.status_code, 409)
            unauthorized = await self.client.post('/api/connect/cancel', headers=self.headers, json={})
            self.assertEqual(unauthorized.status_code, 401)
            cancelled = await self.client.post('/api/connect/cancel', headers=self.admin, json={})
            self.assertTrue(cancelled.json()['cancelled'])
            result = await asyncio.wait_for(task, 2)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()['code'], 'cancelled')
        self.assertTrue(closed.is_set())
        self.assertEqual(self.store.path.read_bytes(), self.original)
        with patch('bridge.protocol.verify_model', AsyncMock(return_value=['ok'])):
            self.assertEqual((await self.client.post('/api/connect', headers=self.admin, json=self.candidate)).status_code, 200)
        self.assertFalse((await self.client.post('/api/connect/cancel', headers=self.admin, json={})).json()['cancelled'])

    async def test_verified_switch_does_not_replace_active_request_or_reset_its_counts(self):
        ready, release = asyncio.Event(), asyncio.Event()
        async def inference(*args):
            ready.set()
            await release.wait()
            return reply()
        with patch('bridge.protocol.create_message', inference), patch('bridge.protocol.verify_model', AsyncMock(return_value=['ok'])):
            task = asyncio.create_task(self.client.post('/v1/messages', headers=self.headers, json=BODY))
            await asyncio.wait_for(ready.wait(), 2)
            result = await self.client.post('/api/connect', headers=self.admin, json=self.candidate)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(self.store.path.read_bytes(), self.original)
            self.assertEqual((await self.stats())['active'], 1)
            release.set()
            self.assertEqual((await asyncio.wait_for(task, 2)).status_code, 200)
        stats = await self.stats()
        self.assertEqual((stats['completed'], stats['failed'], stats['active']), (1, 0, 0))

    async def test_malformed_native_http_responses_are_failed_not_successful(self):
        malformed = [{}, {'status': 'ok'}, {**reply(), 'content': {}}, {**reply(), 'role': 'user'}, {**reply(), 'usage': {'input_tokens': -1, 'output_tokens': 2}}, {**reply(), 'content': [{'type': 'text'}]}, {**reply(), 'stop_reason': None}]
        for value in malformed:
            with self.subTest(value=value), self.transport(lambda request: httpx.Response(200, json=value)):
                result = await self.client.post('/v1/messages', headers=self.headers, json=BODY)
                self.assertEqual(result.status_code, 502)
        stats = await self.stats()
        self.assertEqual((stats['completed'], stats['failed'], stats['active']), (0, len(malformed), 0))
        self.assertIsNone(stats['last_success'])
        self.assertEqual(stats['last_error']['code'], 'invalid_reply')

    async def test_stop_only_stream_is_not_counted_as_success(self):
        with self.transport(lambda request: httpx.Response(200, content=b'data: {"type":"message_stop"}\n\n')):
            result = await self.client.post('/v1/messages', headers=self.headers, json={**BODY, 'stream': True})
        self.assertIn('event: error', result.text)
        stats = await self.stats()
        self.assertEqual((stats['completed'], stats['failed'], stats['active']), (0, 1, 0))

    async def test_completed_stream_closes_without_waiting_for_eof(self):
        closed = asyncio.Event()
        async def source():
            try:
                yield {'type': 'message_start', 'message': {**reply(), 'content': [], 'stop_reason': None}}
                yield {'type': 'message_stop'}
                await asyncio.Future()
            finally:
                closed.set()
        async def consume():
            return [event async for event in stream_events(source())]
        events = await asyncio.wait_for(consume(), .5)
        self.assertEqual(events[-1]['type'], 'message_stop')
        self.assertTrue(closed.is_set())

    async def test_missing_stop_or_malformed_start_is_rejected(self):
        for events in ([{'type': 'message_start', 'message': {}}], [{'type': 'message_start', 'message': {**reply(), 'content': [], 'stop_reason': None}}]):
            async def source():
                for event in events:
                    yield event
            with self.assertRaises(ConnectionFailure):
                _ = [event async for event in stream_events(source())]


class UninstallReliabilityTests(unittest.TestCase):
    def attempt(self, bootout=0, printed=113, health=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {'port': 18766, 'install_id': 'fixture'}
            (root / 'installation.json').write_text(json.dumps(manifest))
            agent = root / 'active.plist'
            agent.write_bytes(b'fixture')
            def run(command, **kwargs):
                return subprocess.CompletedProcess(command, bootout if command[1] == 'bootout' else printed)
            out = io.StringIO()
            error = None
            with patch.object(installer, 'root_dir', return_value=root), patch.object(installer, 'launch_agent', return_value=agent), patch.object(installer.subprocess, 'run', side_effect=run), patch.object(installer.time, 'sleep'), patch.object(installer, 'health', return_value=health), redirect_stdout(out):
                try:
                    installer.uninstall()
                except RuntimeError as exc:
                    error = str(exc)
            return error, out.getvalue(), agent.exists(), (root / 'disabled-launchagent.plist').exists()

    def test_failed_stop_keeps_plist_and_does_not_claim_success(self):
        error, output, active, disabled = self.attempt(bootout=1, printed=0)
        self.assertIsNotNone(error)
        self.assertNotIn('已停止服务', output)
        self.assertTrue(active)
        self.assertFalse(disabled)

    def test_already_stopped_service_can_be_uninstalled(self):
        error, output, active, disabled = self.attempt(bootout=113)
        self.assertIsNone(error)
        self.assertIn('已停止服务', output)
        self.assertFalse(active)
        self.assertTrue(disabled)

    def test_successful_stop_removes_login_entry(self):
        error, output, active, disabled = self.attempt()
        self.assertIsNone(error)
        self.assertIn('已停止服务', output)
        self.assertFalse(active)
        self.assertTrue(disabled)

    def test_waits_for_launchd_and_process_to_finish_exiting(self):
        printed = iter([0, 0, 113, 113])
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, next(printed) if command[1] == 'print' else 0)
        with patch.object(installer.subprocess, 'run', side_effect=run) as commands, patch.object(installer.time, 'sleep') as sleep, patch.object(installer, 'health', side_effect=[{'install_id': 'fixture'}, None]):
            installer.stop_service({'port': 18766, 'install_id': 'fixture'})
        self.assertEqual(commands.call_count, 5)
        self.assertEqual(sleep.call_count, 3)

    def test_unknown_launchctl_error_keeps_plist(self):
        error, output, active, disabled = self.attempt(printed=1)
        self.assertIsNotNone(error)
        self.assertNotIn('已停止服务', output)
        self.assertTrue(active)
        self.assertFalse(disabled)

    def test_live_orphan_process_prevents_false_success(self):
        error, output, active, disabled = self.attempt(health={'install_id': 'fixture'})
        self.assertIsNotNone(error)
        self.assertNotIn('已停止服务', output)
        self.assertTrue(active)
        self.assertFalse(disabled)
