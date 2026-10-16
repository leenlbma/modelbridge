'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');

const ROUTES = Object.freeze({
  '/api/status': { method: 'GET', timeout: 5000 },
  '/api/diagnostics': { method: 'GET', timeout: 5000 },
  '/api/claude-config': { method: 'GET', timeout: 5000 },
  '/api/models': { method: 'POST', timeout: 30000 },
  '/api/connect': { method: 'POST', timeout: 195000 },
  '/api/connect/cancel': { method: 'POST', timeout: 10000 }
});
const LEGACY = Object.freeze({ id: 'custom', name: '旧版连接', base_url: '', protocol: 'responses', auth: 'bearer', model: '', note: '旧版后台未提供服务地址；升级后请重新填写并验证连接。' });
class BridgeError extends Error {
  constructor(message, code = 'offline', unconfirmed = false) {
    super(message); this.code = code; this.unconfirmed = unconfirmed;
  }
}
function requestJSON(port, route, { method = 'GET', token, body, timeout = 3000 } = {}) {
  return new Promise((resolve, reject) => {
    const bytes = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const headers = {};
    if (token) headers.Authorization = 'Bearer ' + token;
    if (bytes) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = bytes.length; }
    let settled = false;
    const finish = (error, value) => {
      if (settled) return; settled = true; clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const req = http.request({ hostname: '127.0.0.1', port, path: route, method, headers, agent: false }, res => {
      const chunks = []; let size = 0;
      res.on('data', chunk => {
        size += chunk.length;
        if (size > 2 * 1024 * 1024) { finish(new BridgeError('后台响应过大，请检查服务。', 'invalid_reply')); res.destroy(); }
        else chunks.push(chunk);
      });
      res.on('end', () => {
        let value;
        try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
        catch { finish(new BridgeError('无法读取后台响应，请刷新状态。', 'invalid_reply', true)); return; }
        if (!value || typeof value !== 'object' || Array.isArray(value)) { finish(new BridgeError('后台响应格式不正确。', 'invalid_reply')); return; }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          finish(new BridgeError(typeof value.message === 'string' ? value.message.slice(0, 1000) : '后台请求失败。', typeof value.code === 'string' ? value.code : 'upstream'));
        } else finish(null, value);
      });
      res.on('error', () => finish(new BridgeError('与后台的连接中断，请刷新状态。', 'offline', true)));
    });
    const timer = setTimeout(() => { finish(new BridgeError('等待后台响应超时，请刷新状态。', 'timeout', true)); req.destroy(); }, timeout);
    req.on('error', () => finish(new BridgeError('后台服务未响应。请确认 ModelBridge 模型连接服务已开启。', 'offline', true)));
    req.end(bytes);
  });
}
function supportsEditing(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version || '');
  if (!match) return false;
  const [major, minor, patch] = match.slice(1).map(Number);
  return major > 0 || minor > 2 || (minor === 2 && patch >= 4);
}
function normalizeStatus(value) {
  const writable = supportsEditing(value.version) && Array.isArray(value.presets);
  if (!Array.isArray(value.presets)) {
    value = { ...value, connection: value.configured ? { ...LEGACY } : null, presets: [{ ...LEGACY }] };
  }
  return { ...value, desktop_capabilities: { edit: writable, cancel: writable },
    desktop_notice: writable ? '' : '当前后台 v' + (value.version || '未知') + ' 较旧：可查看状态、复制连接和导出 Claude 配置；升级到 v0.2.4 或更新后台后，可在此修改连接。' };
}
class Backend {
  constructor({ roots, request = requestJSON } = {}) {
    const support = path.join(os.homedir(), 'Library', 'Application Support');
    this.roots = roots || [path.join(support, 'ModelBridge'), path.join(support, 'HairFree Bridge')];
    this.request = request;
  }
  async resolve() {
    for (const root of this.roots) {
      let manifest;
      try { manifest = JSON.parse(await fs.readFile(path.join(root, 'installation.json'), 'utf8')); }
      catch (error) { if (error.code === 'ENOENT') continue; throw new BridgeError('本地安装记录无法读取，请使用安装入口修复。', 'installation'); }
      if (!Number.isInteger(manifest.port) || manifest.port < 1024 || manifest.port > 65535 || typeof manifest.install_id !== 'string' || !manifest.install_id) {
        throw new BridgeError('本地安装记录不完整，请使用安装入口修复。', 'installation');
      }
      const health = await this.request(manifest.port, '/health');
      if (!['ModelBridge', 'HairFree Bridge'].includes(health.product) || health.status !== 'ok' || health.install_id !== manifest.install_id) {
        throw new BridgeError('这个端口不是记录中的 ModelBridge 服务。请检查后台安装。', 'identity');
      }
      let token;
      try { token = JSON.parse(await fs.readFile(path.join(root, 'data', 'admin.json'), 'utf8')).token; }
      catch { throw new BridgeError('本机管理凭据无法读取，请使用安装入口修复。', 'installation'); }
      if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{20,256}$/.test(token)) throw new BridgeError('本机管理凭据格式不正确。', 'installation');
      return { port: manifest.port, token, health };
    }
    throw new BridgeError('尚未安装后台服务，请先检查本机环境，再按提示准备服务。', 'not_installed');
  }
  async call(route, body) {
    const spec = typeof route === 'string' && Object.hasOwn(ROUTES, route) ? ROUTES[route] : null;
    if (!spec) throw new BridgeError('不支持的客户端操作。', 'invalid_request');
    if (spec.method === 'GET' && body !== undefined) throw new BridgeError('此操作不接受请求参数。', 'invalid_request');
    if (spec.method === 'POST' && (!body || typeof body !== 'object' || Array.isArray(body) || Buffer.byteLength(JSON.stringify(body)) > 8192)) {
      throw new BridgeError('请求参数不完整或过大。', 'invalid_request');
    }
    const target = await this.resolve();
    if (spec.method === 'POST' && !supportsEditing(target.health.version)) {
      throw new BridgeError('旧版后台暂不支持桌面端修改连接，请先升级后台服务。', 'legacy_backend');
    }
    const value = await this.request(target.port, route, { ...spec, token: target.token, body });
    return route === '/api/status' || route === '/api/connect' ? normalizeStatus(value) : value;
  }
}
module.exports = { Backend, BridgeError, requestJSON, normalizeStatus, supportsEditing };
