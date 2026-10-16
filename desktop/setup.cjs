'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { BridgeError } = require('./backend.cjs');

// Client-only updates must not force a healthy, compatible gateway to reinstall.
const MINIMUM_BACKEND = '0.3.0';
function compatible(version, minimum = MINIMUM_BACKEND) {
  if (!/^\d+\.\d+\.\d+$/.test(version || '')) return false;
  const actual = version.split('.').map(Number), required = minimum.split('.').map(Number);
  for (let i = 0; i < 3; i++) { if (actual[i] !== required[i]) return actual[i] > required[i]; }
  return true;
}
class Setup {
  constructor({ backend, resources, version, label, onChange = () => {} }) {
    Object.assign(this, { backend, resources, version, label, onChange });
    this.running = false;
    this.progress = null;
  }
  async installation() {
    for (const root of this.backend.roots) {
      try {
        const manifest = JSON.parse(await fs.readFile(path.join(root, 'installation.json'), 'utf8'));
        if (!Number.isInteger(manifest.port) || manifest.port < 1024 || manifest.port > 65535 || typeof manifest.install_id !== 'string' || !manifest.install_id || typeof manifest.release !== 'string' || !path.resolve(manifest.release).startsWith(path.resolve(root) + path.sep)) throw new Error('invalid');
        return { root, manifest };
      } catch (error) {
        if (error.code !== 'ENOENT') throw new BridgeError('安装记录不完整，无法自动覆盖。请保留数据并检查原安装。', 'installation');
      }
    }
    return { root: this.backend.roots[0], manifest: null };
  }
  serviceLabel(root) { return this.label || (path.basename(root) === 'HairFree Bridge' ? 'com.hairfree.bridge' : 'com.modelbridge.local'); }
  publish(value) { this.progress = value; this.onChange(value); }
  environment(root) {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(UV_|PYTHON|VIRTUAL_ENV|CONDA|PIP_)/.test(key)));
    return { ...env, UV_PYTHON_INSTALL_DIR: path.join(root, 'tools/python'), UV_CACHE_DIR: path.join(root, 'tools/cache'), UV_DEFAULT_INDEX: 'https://pypi.org/simple', PYTHONUNBUFFERED: '1', UV_PYTHON_DOWNLOADS: 'never', UV_NO_PROGRESS: '1' };
  }
  async bootstrap(root) {
    if (!this.resources) throw new BridgeError('此构建不含安装资源。请下载完整客户端。', 'setup');
    const pin = JSON.parse(await fs.readFile(path.join(this.resources, 'bootstrap.json'), 'utf8'));
    const uv = path.join(this.resources, 'uv');
    const bytes = await fs.readFile(uv);
    if (crypto.createHash('sha256').update(bytes).digest('hex') !== pin.binary_sha256) throw new BridgeError('安装工具校验未通过，请重新下载客户端。', 'setup');
    return { pin, uv, bytes, env: this.environment(root) };
  }
  async probePython(candidate, pin, env) {
    if (!path.isAbsolute(candidate)) return null;
    try {
      await fs.access(candidate, 1);
      const code = 'import sys,json,platform,ssl,venv; print(json.dumps({"version":platform.python_version(),"implementation":platform.python_implementation(),"machine":platform.machine(),"base":sys._base_executable}))';
      const value = JSON.parse(await this.command(candidate, ['-I', '-c', code], env, () => {}, 5000));
      if (value.version !== pin.python || value.implementation !== 'CPython' || value.machine !== (process.arch === 'arm64' ? 'arm64' : 'x86_64') || !path.isAbsolute(value.base)) return null;
      await fs.access(value.base, 1);
      // Reuse the base interpreter, never chain a new venv to an older release.
      return value.base;
    } catch { return null; }
  }
  async existingPython(plan) {
    const { manifest, pin, uv, env } = plan;
    if (manifest) {
      const python = await this.probePython(path.join(manifest.release, '.venv/bin/python'), pin, env);
      if (python) return python;
    }
    try {
      // Offline discovery includes app-managed Python and compatible local installations.
      const found = (await this.command(uv, ['--no-config', '--offline', '--no-cache', 'python', 'find', '--system', '--no-python-downloads', pin.python], env, () => {}, 5000)).trim();
      return await this.probePython(found, pin, env);
    } catch { return null; }
  }
  ready(version, detail = '已有后台服务正常，无需下载或重新安装。') {
    return { phase: 'ready', version, detail, checks: { python: '复用现有运行环境', service: 'v' + version + ' · 可直接使用', connection: '本机连接正常' }, network: '本次检查无需下载。' };
  }
  async inspect() {
    const plan = await this.installation();
    let status;
    if (plan.manifest) {
      try { status = await this.backend.call('/api/status'); }
      catch (error) { if (!['offline', 'timeout', 'not_installed'].includes(error.code)) throw error; }
      if (status && compatible(status.version)) return { ...plan, view: this.ready(status.version) };
      if (!status && compatible(plan.manifest.version)) {
        return { ...plan, view: { phase: 'resume', detail: '已安装兼容版本，后台暂未运行。先启动原服务，无需重新下载。', checks: { python: '沿用已安装环境，启动前检查', service: 'v' + plan.manifest.version + ' · 已安装', connection: '待启动' }, network: '启动已有服务无需联网。' } };
      }
    }
    Object.assign(plan, await this.bootstrap(plan.root));
    plan.python = await this.existingPython(plan);
    plan.view = { phase: plan.manifest ? 'upgrade' : 'missing',
      detail: plan.manifest ? '现有后台需要升级，原配置会保留。' : '环境检查完成，可继续准备本机服务。',
      checks: { python: plan.python ? '已检测到 Python ' + plan.pin.python + ' · 将直接复用' : '未找到可用的 Python ' + plan.pin.python + ' · 按需下载', service: plan.manifest ? 'v' + plan.manifest.version + ' → v' + this.version : '尚未安装', connection: '待检查' },
      network: plan.python ? 'Python 无需下载；服务组件优先复用本机缓存。' : '继续后才会下载缺少的环境和组件，需要互联网。' };
    return plan;
  }
  async state() {
    if (this.running && this.progress) return this.progress;
    try {
      const plan = await this.inspect();
      if (plan.view.phase === 'ready') { this.progress = null; return plan.view; }
      return this.progress?.phase === 'failed' ? { ...plan.view, ...this.progress } : plan.view;
    } catch (error) { return { phase: 'unavailable', detail: error instanceof BridgeError ? error.message : '无法检查安装资源，请重新下载完整客户端。' }; }
  }
  async assertIdle() {
    let status;
    try { status = await this.backend.call('/api/status'); }
    catch (error) { if (['offline', 'not_installed', 'timeout'].includes(error.code)) return; throw error; }
    if (status.requests?.active || status.verifying) throw new BridgeError('仍有模型请求或验证正在进行，请结束后再试。', 'busy');
  }
  command(file, args, env, onOutput = () => {}, timeout = 15 * 60 * 1000) {
    return new Promise((resolve, reject) => {
      let output = '', settled = false;
      const child = spawn(file, args, { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
      const finish = error => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(output); };
      const timer = setTimeout(() => { child.kill('SIGTERM'); finish(new BridgeError('操作等待超时，请检查服务状态后重试。', 'setup')); }, timeout);
      child.stdout.on('data', chunk => { output = (output + chunk.toString()).slice(-16384); onOutput(output); });
      child.stderr.on('data', () => {});
      child.on('error', () => finish(new BridgeError('无法启动本机工具，请检查运行环境。', 'setup')));
      child.on('close', code => finish(code === 0 ? null : new BridgeError('操作未完成。请检查网络、磁盘空间和后台项权限，然后重试。已有配置已保留。', 'setup')));
    });
  }
  async install() {
    if (this.running) throw new BridgeError('正在处理，请稍候。', 'busy');
    const repair = this.progress?.action === 'repair';
    this.running = true;
    let resuming = false;
    try {
      this.publish({ phase: 'working', step: 0, detail: '检查后台版本、运行状态和本机环境…', network: '检查阶段不会下载文件。' });
      const plan = await this.inspect();
      if (plan.view.phase === 'ready') { this.publish(plan.view); return this.progress; }
      await this.assertIdle();
      const { root, manifest } = plan;
      if (plan.view.phase === 'resume' && !repair) {
        resuming = true;
        this.publish({ ...plan.view, phase: 'working', step: 3, detail: '启动已安装的后台服务，不下载环境或依赖…' });
        await this.command(path.join(manifest.release, '.venv/bin/python'), [path.join(this.resources, 'backend/installer.py'), 'start', '--root', root, '--label', this.serviceLabel(root)], this.environment(root), () => {}, 75000);
        const status = await this.backend.call('/api/status');
        if (!compatible(status.version)) throw new BridgeError('后台版本检查未通过。', 'setup');
        this.publish(this.ready(status.version, '原服务已恢复，Python 和服务组件均未重新下载。'));
        return this.progress;
      }
      if (!plan.pin) { Object.assign(plan, await this.bootstrap(root)); plan.python = await this.existingPython(plan); }
      const { pin, bytes, env } = plan;
      const toolDir = path.join(root, 'tools');
      await fs.mkdir(toolDir, { recursive: true, mode: 0o700 });
      const uv = path.join(toolDir, 'uv-' + pin.version);
      let same = false;
      try { same = crypto.createHash('sha256').update(await fs.readFile(uv)).digest('hex') === pin.binary_sha256; } catch {}
      if (!same) { await fs.writeFile(uv + '.new', bytes, { mode: 0o700 }); await fs.rename(uv + '.new', uv); }
      let python = plan.python;
      const reused = !!python;
      let checks = { ...plan.view.checks };
      if (python) {
        checks.python = 'Python ' + pin.python + ' · 直接复用';
        this.publish({ phase: 'working', step: 1, detail: '检测到可用 Python，跳过下载。', checks, network: 'Python 无需下载；服务组件优先复用缓存。' });
      } else {
        checks.python = '缺少可用环境 · 正在下载';
        this.publish({ phase: 'working', step: 1, detail: '未检测到可用 Python，正在下载运行环境…', checks, network: '仅下载缺少的环境和组件。' });
        const entries = await fs.readdir(env.UV_PYTHON_INSTALL_DIR).catch(() => []);
        const damaged = entries.some(name => name.startsWith('cpython-' + pin.python + '-'));
        await this.command(uv, ['--no-config', 'python', 'install', ...(damaged ? ['--reinstall'] : []), pin.python], { ...env, UV_PYTHON_DOWNLOADS: 'automatic' });
        python = await this.existingPython({ ...plan, manifest: null, uv });
        if (!python) throw new BridgeError('下载后的 Python 未通过版本与可用性检查，请重试。', 'setup');
        checks.python = 'Python ' + pin.python + ' · 已准备';
      }
      checks.service = '优先检查本机依赖缓存';
      this.publish({ phase: 'working', step: 2, detail: '准备服务组件，优先复用本机缓存…', checks, network: reused ? '已跳过 Python 下载。仅缓存缺少的依赖需要联网。' : '仅缓存缺少的依赖需要联网。' });
      await this.command(python, [path.join(this.resources, 'backend/installer.py'), 'install', '--uv', uv, '--no-open', '--root', root, '--label', this.serviceLabel(root)], env, output => {
        if (output.includes('[2/3]') && this.progress.step !== 3) this.publish({ ...this.progress, step: 3, detail: '启动后台服务并检查连接…', checks: { ...checks, service: '服务组件已准备', connection: '检查中' } });
        else if (output.includes('[dependencies-download]') && this.progress.step === 2) this.publish({ ...this.progress, detail: '本机缓存不完整，下载缺少的服务组件…' });
      });
      const status = await this.backend.call('/api/status');
      if (!compatible(status.version)) throw new BridgeError('服务版本检查未通过，请重试安装。', 'setup');
      this.publish({ ...this.ready(status.version, reused ? '服务已就绪，本次复用了已有 Python。' : '环境与服务已就绪，下次打开无需重复安装。'), checks: { python: checks.python, service: 'v' + status.version + ' · 已就绪', connection: '本机连接正常' } });
      return this.progress;
    } catch (error) {
      this.publish({ ...this.progress, phase: 'failed', ...(resuming ? { action: 'repair' } : {}), detail: resuming ? '原服务未能启动。可以检查并修复组件，已有配置会保留。' : error instanceof BridgeError ? error.message : '操作未完成，请检查环境后重试。已有配置已保留。' });
      return this.progress;
    } finally { this.running = false; }
  }
  async stop() {
    if (this.running) throw new BridgeError('正在处理，请稍候。', 'busy');
    this.running = true;
    try {
      await this.assertIdle();
      const { root, manifest } = await this.installation();
      if (!manifest) return;
      await this.command(path.join(manifest.release, '.venv/bin/python'), [path.join(this.resources, 'backend/installer.py'), 'uninstall', '--root', root, '--label', this.serviceLabel(root)], process.env);
      this.progress = null;
    } finally { this.running = false; }
    this.onChange(await this.state());
  }
}
module.exports = { Setup, compatible };
