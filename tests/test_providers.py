import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from bridge import protocol
from bridge.config import Store, digest, desktop_config
from bridge.providers import PRESETS, DEFAULT_CONNECTION, normalize_connection, valid_model
from bridge.server import create_app

KEY = 'example-test-credential'
CUSTOM = {'id': 'custom', 'name': 'My Gateway', 'base_url': 'https://gateway.example/api/v1', 'protocol': 'chat_completions', 'auth': 'bearer'}


def sse(events):
    return ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()


def chat_reply(text):
    return {'id': 'chat_test', 'object': 'chat.completion', 'created': 1, 'model': 'vendor/model', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 6}}


def native_reply(text):
    return {'id': 'msg_native', 'type': 'message', 'role': 'assistant', 'model': 'vendor/native', 'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 4, 'output_tokens': 2}}


def tool_stream():
    def chunk(delta, finish=None):
        return {'id': 'chat_test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'vendor/model', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
    return sse([
        chunk({'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_test', 'type': 'function', 'function': {'name': 'echo_value', 'arguments': ''}}]}),
        chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '{"value":'}}]}),
        chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '"bridge-ready"}'}}]}),
        chunk({}, 'tool_calls'),
    ]) + b'data: [DONE]\n\n'


class ProviderConfigTests(unittest.TestCase):
    def test_model_ids_and_base_url_validation(self):
        for model in ('vendor/model-1:free', 'qwen3:8b', 'claude-sonnet-4-5', 'gpt-example'):
            self.assertTrue(valid_model(model))
        for model in ('', 'model name', 'model\nkey', 'model?api_key=secret'):
            self.assertFalse(valid_model(model))
        for url in ('http://remote.example/v1', 'https://user:pass@host/v1', 'https://host/v1?key=secret', 'https://host/#secret', 'https://host/v1/responses', 'https://host:invalid/v1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_connection({**CUSTOM, 'base_url': url})
        for url in ('http://localhost:8000/v1/', 'http://127.0.0.1:8000/v1', 'http://[::1]:8000/v1'):
            self.assertFalse(normalize_connection({**CUSTOM, 'base_url': url})['base_url'].endswith('/'))

    def test_presets_validate_without_shipping_keys(self):
        for preset in PRESETS:
            if preset['id'] != 'custom':
                self.assertEqual(normalize_connection(preset)['id'], preset['id'])
            self.assertNotIn('key', preset)
        with self.assertRaises(ValueError):
            normalize_connection({**CUSTOM, 'auth': 'x-api-key'})

    def test_legacy_profile_requires_explicit_endpoint_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'profile.json'
            legacy = {'schema': 1, 'model': 'gpt-example', 'key_sha256': digest(KEY), 'verified_at': 'old'}
            path.write_text(json.dumps(legacy))
            original = path.read_bytes()
            store = Store(temp)
            self.assertIsNone(store.profile)
            self.assertTrue(store.load_error)
            self.assertFalse(store.accepts(KEY))
            self.assertEqual(path.read_bytes(), original)
            store.save(KEY, 'vendor/model', 'now', CUSTOM)
            restored = Store(temp)
            self.assertEqual(restored.profile['connection'], CUSTOM)
            self.assertNotIn(KEY, path.read_text())
            self.assertEqual(restored.profile['schema'], 2)

    def test_export_uses_actual_connection_name(self):
        result = desktop_config(18780, 'vendor/model', CUSTOM)
        self.assertIn('vendor/model (My Gateway)', json.dumps(result))
        self.assertNotIn(CUSTOM['base_url'], json.dumps(result))
        self.assertNotIn(KEY, json.dumps(result))

    def test_explicit_old_provider_endpoint_survives_as_custom_without_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            store.save(KEY, 'vendor/model', 'before', CUSTOM)
            value = json.loads(store.path.read_text())
            value['connection']['id'] = 'retired-private-provider'
            store.path.write_text(json.dumps(value))
            original = store.path.read_bytes()
            restored = Store(temp)
            self.assertEqual(restored.profile['connection'], CUSTOM)
            self.assertTrue(restored.accepts(KEY))
            self.assertEqual(store.path.read_bytes(), original)


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_actual_adapter_text_tools_and_result_over_mock_http(self):
        requests = []
        def response(output, status='completed'):
            return {'id': 'resp_test', 'object': 'response', 'created_at': 1, 'status': status, 'model': 'vendor/model', 'output': output, 'parallel_tool_calls': True, 'tool_choice': 'auto', 'tools': [], 'usage': {'input_tokens': 4, 'output_tokens': 2, 'total_tokens': 6}}
        async def send(client, request, **kwargs):
            body = json.loads(request.content); requests.append(body)
            self.assertEqual(str(request.url), CUSTOM['base_url'] + '/responses')
            self.assertEqual(request.headers['authorization'], 'Bearer ' + KEY)
            self.assertEqual(body['model'], 'vendor/model')
            if len(requests) == 2:
                call = {'type': 'function_call', 'id': 'fc_test', 'call_id': 'call_test', 'name': 'echo_value', 'arguments': '{"value":"bridge-ready"}', 'status': 'completed'}
                events = [
                    {'type': 'response.created', 'response': response([], 'in_progress')},
                    {'type': 'response.output_item.added', 'output_index': 0, 'item': {**call, 'arguments': '', 'status': 'in_progress'}},
                    {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'item_id': 'fc_test', 'delta': '{"value":'},
                    {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'item_id': 'fc_test', 'delta': '"bridge-ready"}'},
                    {'type': 'response.function_call_arguments.done', 'output_index': 0, 'item_id': 'fc_test', 'arguments': call['arguments']},
                    {'type': 'response.output_item.done', 'output_index': 0, 'item': call},
                    {'type': 'response.completed', 'response': response([call])},
                ]
                return httpx.Response(200, request=request, headers={'content-type': 'text/event-stream'}, content=sse([{**event, 'sequence_number': i} for i, event in enumerate(events)]))
            if len(requests) == 3:
                results = [item for item in body['input'] if item['type'] == 'function_call_output']
                self.assertEqual(results[0]['call_id'], 'call_test')
                self.assertIn('bridge-ready', str(results[0]['output']))
            output = [{'type': 'message', 'id': 'msg_test', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': 'OK' if len(requests) == 1 else 'bridge-ready', 'annotations': []}]}]
            return httpx.Response(200, request=request, json=response(output))
        with patch.object(httpx.AsyncClient, 'send', send):
            checks = await protocol.verify_model(KEY, 'vendor/model', {**CUSTOM, 'protocol': 'responses'})
        self.assertEqual(len(checks), 3)
        self.assertEqual(len(requests), 3)

    def transport(self, handler):
        original = httpx.AsyncClient
        return patch('bridge.protocol.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    async def test_chat_text_streamed_tools_and_result_use_selected_endpoint(self):
        requests = []
        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertEqual(str(request.url), CUSTOM['base_url'] + '/chat/completions')
            self.assertEqual(request.headers['authorization'], 'Bearer ' + KEY)
            self.assertEqual(body['model'], 'vendor/model')
            if len(requests) == 2:
                self.assertEqual(body['tool_choice']['function']['name'], 'echo_value')
                self.assertEqual(body['tools'][0]['function']['name'], 'echo_value')
                return httpx.Response(200, content=tool_stream(), headers={'content-type': 'text/event-stream'})
            if len(requests) == 3:
                results = [m for m in body['messages'] if m['role'] == 'tool']
                self.assertEqual(results[0]['tool_call_id'], 'call_test')
                self.assertEqual(results[0]['content'], 'bridge-ready')
            return httpx.Response(200, json=chat_reply('OK' if len(requests) == 1 else 'bridge-ready'))
        with self.transport(handler):
            checks = await protocol.verify_model(KEY, 'vendor/model', CUSTOM)
        self.assertEqual(len(checks), 3)
        self.assertEqual(len(requests), 3)

    async def test_native_messages_auth_model_and_stream(self):
        native = {**CUSTOM, 'protocol': 'anthropic', 'auth': 'x-api-key'}
        requests = []
        def handler(request):
            body = json.loads(request.content); requests.append(body)
            self.assertEqual(str(request.url), CUSTOM['base_url'] + '/messages')
            self.assertEqual(request.headers['x-api-key'], KEY)
            self.assertEqual(request.headers['anthropic-version'], '2023-06-01')
            self.assertNotIn('authorization', request.headers)
            self.assertEqual(body['model'], 'vendor/native')
            if len(requests) == 2:
                events = [{'type': 'message_start', 'message': {**native_reply(''), 'content': [], 'stop_reason': None}}, {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'call_native', 'name': 'echo_value', 'input': {}}}, {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': '{"value":"bridge-ready"}'}}, {'type': 'message_stop'}]
                return httpx.Response(200, content=sse(events))
            if len(requests) == 3:
                self.assertEqual(body['messages'][-2]['content'][0]['tool_use_id'], 'call_native')
            return httpx.Response(200, json=native_reply('OK' if len(requests) == 1 else 'bridge-ready'))
        with self.transport(handler):
            self.assertEqual(len(await protocol.verify_model(KEY, 'vendor/native', native)), 3)

    async def test_discovery_accepts_non_gpt_ids_and_native_auth(self):
        connection = {**CUSTOM, 'protocol': 'anthropic', 'auth': 'x-api-key'}
        def handler(request):
            self.assertTrue(str(request.url).endswith('/models'))
            self.assertEqual(request.headers['x-api-key'], KEY)
            return httpx.Response(200, json={'data': [{'id': 'vendor/model'}, {'id': 'qwen3:8b'}]})
        with self.transport(handler):
            self.assertEqual(await protocol.model_list(KEY, connection), ['vendor/model', 'qwen3:8b'])

    async def test_chat_premature_stream_is_rejected(self):
        def handler(request):
            chunk = {'id': 'partial', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'm', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'partial'}, 'finish_reason': None}]}
            return httpx.Response(200, content=sse([chunk]) + b'data: [DONE]\n\n')
        with self.transport(handler):
            result = await protocol.create_message('m', KEY, {'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 32, 'stream': True}, CUSTOM)
            with self.assertRaises(ConnectionFailure):
                _ = [x async for x in protocol.stream_events(result)]

    async def test_redirect_does_not_forward_credentials(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(307, headers={'location': 'https://other.example/steal'})
        with self.transport(handler), self.assertRaises(ConnectionFailure):
            await protocol.create_message('m', KEY, {'messages': [{'role': 'user', 'content': 'Hi'}]}, CUSTOM)
        self.assertEqual(len(calls), 1)

    async def test_chat_does_not_forward_anthropic_only_fields(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertNotIn('context_management', body)
            self.assertNotIn('top_k', body)
            self.assertNotIn('system', body)
            self.assertEqual(body['messages'][0]['role'], 'system')
            return httpx.Response(200, json=chat_reply('OK'))
        with self.transport(handler):
            reply = await protocol.create_message('vendor/model', KEY, {'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 128, 'system': 'Be helpful.', 'top_k': 5, 'context_management': {'edits': []}}, CUSTOM)
        self.assertEqual(reply['content'][0]['text'], 'OK')


from bridge.protocol import ConnectionFailure


class ProviderApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_request_cannot_forward_old_key_after_provider_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(temp); store = app.state.store
            store.save(KEY, 'old-model', 'old')
            async def body():
                store.save('replacement-key', 'new-model', 'new', CUSTOM)
                yield json.dumps({'model': 'claude-sonnet-4-5', 'messages': [{'role': 'user', 'content': 'hi'}]}).encode()
            create = AsyncMock()
            with patch('bridge.protocol.create_message', create):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1)), base_url='http://127.0.0.1:18766') as client:
                    reply = await client.post('/v1/messages', headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}, content=body())
            self.assertEqual(reply.status_code, 401)
            create.assert_not_called()

    async def test_failed_switch_keeps_provider_and_key_and_diagnostics_redact_url(self):
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(temp)
            store = app.state.store
            store.save(KEY, 'vendor/old', 'old-time', CUSTOM)
            previous = store.path.read_bytes()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1)), base_url='http://127.0.0.1:18766') as client:
                admin = {'Authorization': 'Bearer ' + store.admin}
                with patch('bridge.protocol.verify_model', AsyncMock(side_effect=ConnectionFailure('invalid_reply'))):
                    result = await client.post('/api/connect', headers=admin, json={'key': 'replacement-key', 'model': 'new/model', 'connection': DEFAULT_CONNECTION})
                self.assertEqual(result.status_code, 502)
                self.assertEqual(store.path.read_bytes(), previous)
                self.assertTrue(store.accepts(KEY))
                report = await client.get('/api/diagnostics', headers=admin)
                self.assertNotIn(CUSTOM['base_url'], report.text)
                self.assertNotIn(CUSTOM['name'], report.text)
                self.assertNotIn(KEY, report.text)
                create = AsyncMock(return_value=native_reply('ok'))
                with patch('bridge.protocol.create_message', create):
                    await client.post('/v1/messages', headers={'Authorization': 'Bearer ' + KEY}, json={'model': 'claude-sonnet-4-5', 'messages': [{'role': 'user', 'content': 'hi'}], 'connection': DEFAULT_CONNECTION})
                self.assertEqual(create.call_args.args[3], CUSTOM)

    async def test_successful_switch_resets_client_status_and_key(self):
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(temp); store = app.state.store
            store.save(KEY, 'gpt-old', 'old')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1)), base_url='http://127.0.0.1:18766') as client:
                with patch('bridge.protocol.verify_model', AsyncMock(return_value=['text', 'tools', 'result'])):
                    response = await client.post('/api/connect', headers={'Authorization': 'Bearer ' + store.admin}, json={'key': 'replacement-key', 'model': 'vendor/new', 'connection': CUSTOM})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['connection'], CUSTOM)
                self.assertEqual(response.json()['requests']['completed'], 0)
                self.assertFalse(store.accepts(KEY))
                self.assertTrue(store.accepts('replacement-key'))
