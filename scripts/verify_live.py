"""Opt-in live compatibility check. Reads a Key without echo and never saves it."""
import argparse
import asyncio
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from bridge import __version__
from bridge.config import ROUTE
from bridge.providers import PRESETS
from bridge.server import create_app
from bridge import protocol


async def check(key, provider, model):
    connection = next(p for p in PRESETS if p['id'] == provider)
    report = {'version': __version__, 'tested_at': datetime.now(timezone.utc).isoformat(), 'provider': provider, 'model': model, 'protocol': connection['protocol'], 'client': 'synthetic_messages_client', 'passed': False, 'checks': [], 'upstream_http': []}
    original_send = httpx.AsyncClient.send
    original_describe = protocol.describe
    def observed_failure(exc):
        report['failure_trace'] = {'exception_type': type(exc).__name__, 'frames': [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name} for frame in traceback.extract_tb(exc.__traceback__)[-6:]]}
        return original_describe(exc)
    async def observed_send(client, request, **kwargs):
        if not str(request.url).startswith(connection['base_url'] + '/'):
            return await original_send(client, request, **kwargs)
        item = {'request_number': len(report['upstream_http']) + 1}
        report['upstream_http'].append(item)
        try:
            response = await original_send(client, request, **kwargs)
            item['http_status'] = response.status_code
            return response
        except Exception as exc:
            item['exception_type'] = type(exc).__name__
            raise
    start = time.monotonic()
    try:
        async with asyncio.timeout(240):
            with tempfile.TemporaryDirectory(prefix='modelbridge-live-') as root, patch.object(httpx.AsyncClient, 'send', observed_send), patch.object(protocol, 'describe', observed_failure):
                app = create_app(root)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:18766') as client:
                    admin = {'Authorization': 'Bearer ' + app.state.store.admin}
                    response = await client.post('/api/connect', headers=admin, json={'key': key, 'model': model, 'connection': connection})
                    if response.status_code != 200:
                        report['failure'] = {'stage': 'connection_verification', 'http_status': response.status_code, 'code': response.json().get('code', 'unknown')}
                        return report
                    report['checks'].extend(response.json()['checks'])
                    for stream in [False, True]:
                        response = await client.post('/v1/messages', headers={'x-api-key': key}, json={'model': ROUTE, 'max_tokens': 128, 'stream': stream, 'messages': [{'role': 'user', 'content': 'Reply with exactly OK.'}]})
                        valid = response.status_code == 200 and ('event: message_stop' in response.text and 'event: error' not in response.text if stream else response.json().get('type') == 'message')
                        if not valid:
                            report['failure'] = {'stage': 'client_stream' if stream else 'client_message', 'http_status': response.status_code}
                            return report
                        report['checks'].append('客户端流式请求' if stream else '客户端普通请求')
                    report['requests'] = (await client.get('/api/status', headers=admin)).json()['requests']
                    report['passed'] = report['requests']['completed'] == 2 and report['requests']['failed'] == 0
    except TimeoutError:
        report['failure'] = {'stage': 'overall', 'code': 'timeout'}
    except Exception:
        # Exceptions can contain headers or request contents. Keep reports generic.
        report['failure'] = {'stage': 'overall', 'code': 'local_or_transport_error'}
    finally:
        report['duration_seconds'] = round(time.monotonic() - start, 2)
    return report


def main():
    parser = argparse.ArgumentParser(description='发送少量真实模型请求，验证三步工具调用和 Messages 网关；不等同于 Claude 桌面端验收。')
    parser.add_argument('--provider', choices=[p['id'] for p in PRESETS if p['id'] != 'custom'], required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    key = getpass.getpass('服务商 Key（隐藏输入，仅用于本次请求）: ')
    report = asyncio.run(check(key, args.provider, args.model))
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
