'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const { Backend, requestJSON, BridgeError } = require('../desktop/backend.cjs');
const TOKEN = 'desktop-fixture-token-1234567890';
async function fixture(t, { version = '0.2.5', installID = 'fixture-id', responseID = installID } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'modelbridge-desktop-test-'));
  const calls = [];
  const server = http.createServer((req, res) => {
    let raw = ''; req.on('data', chunk => raw += chunk);
    req.on('end', () => {
      calls.push({ route: req.url, authorization: req.headers.authorization, body: raw ? JSON.parse(raw) : undefined });
      res.setHeader('Content-Type', 'application/json');
      if (req.url === '/health') res.end(JSON.stringify({ product: 'ModelBridge', status: 'ok', version, install_id: responseID }));
      else if (req.headers.authorization !== 'Bearer ' + TOKEN) { res.writeHead(401); res.end('{}'); }
      else if (req.url === '/api/status') res.end(JSON.stringify({ product: 'ModelBridge', version, configured: true, model: 'fixture-model', requests: { completed: 0, failed: 0, active: 0 }, ...(version === '0.1.0' ? {} : { presets: [{ id: 'custom' }], connection: { id: 'custom' } }) }));
      else res.end(JSON.stringify({ ok: true }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await fs.mkdir(path.join(root, 'data'));
  await fs.writeFile(path.join(root, 'installation.json'), JSON.stringify({ port, install_id: installID }));
  await fs.writeFile(path.join(root, 'data', 'admin.json'), JSON.stringify({ token: TOKEN }));
  t.after(async () => { await new Promise(resolve => server.close(resolve)); await fs.rm(root, { recursive: true, force: true }); });
  return { backend: new Backend({ roots: [root] }), calls, root, port };
}
test('uses the configured local port, verifies service identity, keeps admin token out of response', async t => {
  const { backend, calls } = await fixture(t);
  const value = await backend.call('/api/status');
  assert.equal(value.desktop_capabilities.edit, true);
  assert.equal(calls[0].route, '/health');
  assert.equal(calls[0].authorization, undefined);
  assert.equal(calls[1].authorization, 'Bearer ' + TOKEN);
  assert.equal(JSON.stringify(value).includes(TOKEN), false);
});
test('does not send credentials to an unrelated service on the old port', async t => {
  const { backend, calls } = await fixture(t, { responseID: 'different-service' });
  await assert.rejects(backend.call('/api/status'), { code: 'identity' });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].authorization, undefined);
});
test('legacy service can be viewed but cannot receive changed provider keys', async t => {
  const { backend, calls } = await fixture(t, { version: '0.1.0' });
  const value = await backend.call('/api/status');
  assert.equal(value.desktop_capabilities.edit, false);
  assert.equal(value.connection.id, 'custom');
  assert.equal(value.connection.base_url, '');
  await assert.rejects(backend.call('/api/connect', { key: 'new-provider-key', model: 'new-model' }), { code: 'legacy_backend' });
  assert.equal(calls.filter(c => c.route === '/api/connect').length, 0);
});
test('rejects external URLs, prototype property names and oversized bodies before accessing backend', async () => {
  const backend = new Backend({ roots: [] });
  for (const route of ['https://example.com', '/v1/messages', '/api/status?x=1', '__proto__', 'constructor']) {
    await assert.rejects(backend.call(route), { code: 'invalid_request' });
  }
  await assert.rejects(backend.call('/api/models', { key: 'x'.repeat(9000) }), { code: 'invalid_request' });
  await assert.rejects(backend.call('/api/status', {}), { code: 'invalid_request' });
});
test('reports missing installation and does not create data or start another gateway', async () => {
  const backend = new Backend({ roots: [path.join(os.tmpdir(), 'modelbridge-no-install-' + Date.now())] });
  await assert.rejects(backend.call('/api/status'), { code: 'not_installed' });
});
test('modern verification and cancellation use explicit POST routes and preserve bodies', async t => {
  const { backend, calls } = await fixture(t);
  const body = { key: 'fake-key', model: 'fixture', connection: { id: 'custom' } };
  await backend.call('/api/models', body);
  await backend.call('/api/connect/cancel', {});
  assert.deepEqual(calls.find(c => c.route === '/api/models').body, body);
  assert.deepEqual(calls.find(c => c.route === '/api/connect/cancel').body, {});
});
test('uses an overall timeout even if the backend never responds', async t => {
  const server = http.createServer((_req, _res) => {});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  await assert.rejects(requestJSON(server.address().port, '/health', { timeout: 30 }), { code: 'timeout', unconfirmed: true });
});

const { Setup } = require('../desktop/setup.cjs');
const crypto = require('node:crypto');
async function setupFixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'modelbridge-setup-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const resources = path.join(root, 'resources'), service = path.join(root, 'service');
  await fs.mkdir(resources);
  const bytes = Buffer.from('fixture verified tool');
  await fs.writeFile(path.join(resources, 'uv'), bytes);
  await fs.writeFile(path.join(resources, 'bootstrap.json'), JSON.stringify({ version: 'test', python: '3.12.13', binary_sha256: crypto.createHash('sha256').update(bytes).digest('hex') }));
  const backend = { roots: [service], call: async () => { throw new BridgeError('missing', 'not_installed'); } };
  const setup = new Setup({ backend, resources, version: '0.3.0', label: 'com.modelbridge.acceptance.fixture' });
  return { root, resources, service, setup, backend };
}
async function installedFixture(t, version = '0.3.0', online = true) {
  const value = await setupFixture(t);
  await fs.mkdir(value.service);
  value.manifest = { version, port: 18779, install_id: 'existing', release: path.join(value.service, 'releases/existing') };
  await fs.writeFile(path.join(value.service, 'installation.json'), JSON.stringify(value.manifest));
  if (online) value.backend.call = async () => ({ version, requests: { active: 0 } });
  value.setup.version = '0.3.1';
  return value;
}
test('compatible running service skips all installers, including after client-only updates', async t => {
  const { setup, service, manifest } = await installedFixture(t);
  setup.command = () => assert.fail('healthy service must not execute installers or probes');
  assert.equal((await setup.state()).phase, 'ready');
  assert.equal((await setup.install()).phase, 'ready');
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(service, 'installation.json'))), manifest);
  await assert.rejects(fs.access(path.join(service, 'tools')));
});
test('newer compatible backend is not downgraded', async t => {
  const { setup } = await installedFixture(t, '0.4.0');
  setup.command = () => assert.fail('must not downgrade');
  assert.equal((await setup.install()).version, '0.4.0');
});
test('legacy upgrade reuses existing Python without any python install command', async t => {
  const { setup, backend } = await installedFixture(t, '0.1.0');
  const commands = [], updates = [];
  setup.onChange = value => updates.push(value);
  setup.existingPython = async () => '/fixture/base/python';
  setup.command = async (file, args, env, output) => {
    commands.push({ file, args, env });
    assert.ok(args.includes('--no-open')); output('[2/3]');
    backend.call = async () => ({ version: '0.3.1' }); return '';
  };
  assert.match((await setup.state()).checks.python, /直接复用/);
  assert.equal((await setup.install()).phase, 'ready');
  assert.equal(commands.length, 1);
  assert.equal(commands[0].file, '/fixture/base/python');
  assert.equal(commands[0].env.UV_PYTHON_DOWNLOADS, 'never');
  assert.ok(updates.some(x => /跳过下载/.test(x.detail)));
});
test('missing Python downloads once then validates and installs scoped service', async t => {
  const { setup, backend, service } = await setupFixture(t);
  let downloaded = false; const commands = [];
  setup.existingPython = async () => downloaded ? '/fixture/new/python' : null;
  setup.command = async (file, args, env) => {
    commands.push({ file, args, env });
    if (args.includes('python') && args.includes('install')) downloaded = true;
    else { assert.ok(args.includes('--no-open')); backend.call = async () => ({ version: '0.3.0' }); }
    return '';
  };
  assert.equal((await setup.install()).phase, 'ready');
  assert.equal(commands.length, 2);
  assert.equal(commands[0].env.UV_PYTHON_DOWNLOADS, 'automatic');
  assert.equal(commands[1].args[commands[1].args.indexOf('--root') + 1], service);
  assert.equal(commands[1].args[commands[1].args.indexOf('--label') + 1], 'com.modelbridge.acceptance.fixture');
});
test('a corrupt app-managed Python is explicitly repaired after detection fails', async t => {
  const { setup, backend, service } = await setupFixture(t);
  await fs.mkdir(path.join(service, 'tools/python/cpython-3.12.13-macos-aarch64-none'), { recursive: true });
  let downloaded = false;
  setup.existingPython = async () => downloaded ? '/fixture/repaired/python' : null;
  setup.command = async (_file, args) => {
    if (args.includes('python')) { assert.ok(args.includes('--reinstall')); downloaded = true; }
    else backend.call = async () => ({ version: '0.3.0' });
    return '';
  };
  assert.equal((await setup.install()).phase, 'ready');
});
test('stopped compatible service starts original release without downloads or new manifest', async t => {
  const { setup, backend, service, manifest } = await installedFixture(t, '0.3.0', false);
  const commands = [];
  setup.bootstrap = () => assert.fail('restart must not need bootstrap tool');
  setup.command = async (file, args) => { commands.push({ file, args }); backend.call = async () => ({ version: '0.3.0' }); return ''; };
  assert.equal((await setup.state()).phase, 'resume');
  assert.equal((await setup.install()).phase, 'ready');
  assert.equal(commands.length, 1);
  assert.equal(commands[0].file, path.join(manifest.release, '.venv/bin/python'));
  assert.equal(commands[0].args[1], 'start');
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(service, 'installation.json'))), manifest);
});
test('failed start offers repair but does not silently reinstall', async t => {
  const { setup, backend } = await installedFixture(t, '0.3.0', false);
  const commands = [];
  setup.command = async (_file, args) => { commands.push(args); throw new Error('failed start'); };
  const result = await setup.install();
  assert.equal(result.action, 'repair'); assert.equal(commands.length, 1); assert.equal(commands[0][1], 'start');
  setup.existingPython = async () => '/fixture/base/python';
  setup.command = async (_file, args) => { commands.push(args); assert.equal(args[1], 'install'); backend.call = async () => ({ version: '0.3.1' }); return ''; };
  assert.equal((await setup.install()).phase, 'ready');
  assert.equal(commands.length, 2);
});
test('checks are offline, and candidate Python must match version, architecture and capabilities', async t => {
  const { setup, root } = await setupFixture(t);
  const candidate = path.join(root, 'python'); await fs.writeFile(candidate, 'fixture', { mode: 0o700 });
  const pin = { python: '3.12.13' }, env = setup.environment(root);
  const info = { version: '3.12.13', implementation: 'CPython', machine: process.arch === 'arm64' ? 'arm64' : 'x86_64', base: candidate };
  setup.command = async (_file, args, commandEnv) => {
    if (args.includes('find')) { assert.ok(args.includes('--offline')); assert.ok(args.includes('--no-python-downloads')); assert.equal(commandEnv.UV_PYTHON_DOWNLOADS, 'never'); return candidate; }
    assert.ok(args.includes('-I')); return JSON.stringify(info);
  };
  assert.equal(await setup.existingPython({ manifest: null, pin, env, uv: '/fixture/uv' }), candidate);
  info.version = '3.9.6'; assert.equal(await setup.probePython(candidate, pin, env), null);
  info.version = '3.12.13'; info.machine = 'unsupported'; assert.equal(await setup.probePython(candidate, pin, env), null);
});
test('setup rejects altered bootstrap before executing it', async t => {
  const { setup, resources } = await setupFixture(t);
  await fs.writeFile(path.join(resources, 'uv'), 'altered'); setup.command = () => assert.fail('must not execute');
  assert.equal((await setup.install()).phase, 'failed'); assert.match(setup.progress.detail, /校验/);
});
test('download failure preserves existing manifest and allows retry', async t => {
  const { setup, service, manifest } = await installedFixture(t, '0.1.0');
  setup.existingPython = async () => null;
  let count = 0; setup.command = async () => { count++; throw new Error('private path or credential'); };
  assert.equal((await setup.install()).phase, 'failed');
  assert.equal(setup.running, false);
  await setup.install(); assert.equal(count, 2);
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(service, 'installation.json'))), manifest);
  assert.doesNotMatch(setup.progress.detail, /private path|credential/);
});
test('active inference prevents required upgrade; concurrent installation is rejected', async t => {
  const { setup, backend } = await installedFixture(t, '0.1.0');
  backend.call = async () => ({ version: '0.1.0', requests: { active: 1 } });
  setup.existingPython = async () => '/fixture/base/python'; setup.command = () => assert.fail('must not run installer');
  assert.equal((await setup.install()).phase, 'failed'); assert.match(setup.progress.detail, /请求/);
  setup.running = true; await assert.rejects(setup.install(), { code: 'busy' });
});
test('malformed manifest cannot be overwritten', async t => {
  const { setup, service } = await setupFixture(t);
  await fs.mkdir(service); await fs.writeFile(path.join(service, 'installation.json'), '{}');
  assert.equal((await setup.state()).phase, 'unavailable'); setup.command = () => assert.fail('must not run');
  assert.equal((await setup.install()).phase, 'failed');
});
test('a recovered backend clears stale installation failure', async t => {
  const { setup } = await installedFixture(t);
  setup.progress = { phase: 'failed', detail: 'past failure' };
  assert.equal((await setup.state()).phase, 'ready'); assert.equal(setup.progress, null);
});
