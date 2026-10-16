'use strict';
const { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, protocol, dialog, clipboard, session } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { Backend, BridgeError } = require('./backend.cjs');
const { Setup } = require('./setup.cjs');
const crypto = require('node:crypto');
const ORIGIN = 'modelbridge://app';
const PAGE = ORIGIN + '/index.html';
const WEB = path.join(__dirname, '..', 'web');
const ASSETS = { '/index.html': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8', '/icon.svg': 'image/svg+xml' };
const CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";
protocol.registerSchemesAsPrivileged([{ scheme: 'modelbridge', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setName('ModelBridge');
const rootFlag = process.argv.find(value => value.startsWith('--service-dir='));
const labelFlag = process.argv.find(value => value.startsWith('--setup-label='));
const backend = new Backend(rootFlag ? { roots: [path.resolve(rootFlag.slice('--service-dir='.length))] } : undefined);
app.setPath('userData', path.join(app.getPath('appData'), 'ModelBridge Desktop' + (rootFlag ? '-test-' + crypto.createHash('sha256').update(rootFlag).digest('hex').slice(0, 10) : '')));
const setup = new Setup({ backend, resources: rootFlag && !labelFlag ? null : path.join(__dirname, '..', 'bootstrap'),
  version: app.getVersion(), label: labelFlag?.slice('--setup-label='.length),
  onChange: value => { if (win && !win.isDestroyed()) win.webContents.send('bridge:setup-state', value); }
});
let win, tray, quitting = false, confirmingQuit = false, poll, busy = false, dirty = false;
let statusLabel = '正在连接后台服务…';
function trusted(event) { return win && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame && event.senderFrame.url.split('#')[0] === PAGE; }
function guard(event) { if (!trusted(event)) throw new Error('Unsupported sender'); }
function errorValue(error) { return { ok: false, message: error instanceof BridgeError ? error.message : '操作未完成，请重试。', code: error.code || 'local', unconfirmed: error.unconfirmed === true }; }
function show(panel) {
  if (!win) createWindow();
  if (win.isMinimized()) win.restore();
  win.show(); win.focus();
  if (panel) win.webContents.send('bridge:navigate', panel);
}
function refreshTray() {
  if (!tray) return;
  tray.setToolTip('ModelBridge · ' + statusLabel);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: statusLabel, enabled: false }, { type: 'separator' },
    { label: '打开 ModelBridge', click: () => show() },
    { label: '接入 Claude', click: () => show('claude') },
    { label: '状态与帮助', click: () => show('help') },
    { type: 'separator' }, { label: '退出客户端（后台服务继续运行）', click: () => app.quit() }
  ]));
}
async function pollStatus() {
  try {
    const status = await backend.call('/api/status');
    statusLabel = status.configured ? '已连接 · ' + status.model : '后台运行中 · 待配置';
  } catch { statusLabel = '后台服务不可用'; }
  refreshTray();
}
function createWindow() {
  win = new BrowserWindow({ width: 1060, height: 790, minWidth: 860, minHeight: 650, show: false,
    title: 'ModelBridge', titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 20, y: 19 },
    backgroundColor: '#00000000', vibrancy: 'sidebar', visualEffectState: 'followWindow',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true,
      nodeIntegration: false, sandbox: true, webSecurity: true, spellcheck: false,
      additionalArguments: ['--modelbridge-version=' + app.getVersion()] }
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event, url) => { if (url.split('#')[0] !== PAGE) event.preventDefault(); });
  win.webContents.on('will-attach-webview', event => event.preventDefault());
  win.once('ready-to-show', () => win.show());
  win.on('close', event => { if (!quitting) { event.preventDefault(); win.hide(); } });
  win.on('closed', () => { win = null; });
  win.loadURL(PAGE).catch(() => dialog.showErrorBox('ModelBridge', '无法打开客户端界面，请重新解压安装包。'));
}
function installIPC() {
  ipcMain.handle('bridge:setup-state', async event => { guard(event); return setup.state(); });
  ipcMain.handle('bridge:setup-install', async event => {
    guard(event);
    if (busy || dirty) return { phase: 'failed', detail: '请先完成验证或还原未保存的更改，再安装后台服务。' };
    const current = await setup.state();
    if (['ready', 'unavailable', 'working'].includes(current.phase)) return current;
    const { manifest } = await setup.installation();
    if (manifest) {
      const resume = current.phase === 'resume' && current.action !== 'repair';
      const result = await dialog.showMessageBox(win, { type: 'question', buttons: ['取消', resume ? '启动已有服务' : '继续准备服务'], defaultId: 0, cancelId: 0,
        message: resume ? '启动已安装的后台服务？' : '更新或修复 ModelBridge 后台服务？',
        detail: resume ? '将检查并启动原服务，恢复登录启动。不会下载 Python 或重新安装依赖。' : '先检查并复用已有环境，仅补齐缺少的组件。完成后会短暂重启后台；请先结束 Claude 当前回复。原有配置会保留。' });
      if (result.response !== 1) return current;
    }
    const result = await setup.install();
    pollStatus(); return result;
  });
  ipcMain.handle('bridge:setup-stop', async event => {
    guard(event);
    if (busy || dirty || setup.running) return { ok: false, message: '请先完成当前操作。' };
    const selected = await dialog.showMessageBox(win, { type: 'warning', buttons: ['取消', '停用后台服务'], defaultId: 0, cancelId: 0,
      message: '停止模型连接并取消登录启动？', detail: '请先在 Claude 切换到其他连接。配置和运行文件将保留，可以通过客户端重新安装恢复。' });
    if (selected.response !== 1) return { ok: true, canceled: true };
    try { await setup.stop(); pollStatus(); return { ok: true }; } catch (error) { return errorValue(error); }
  });
  ipcMain.handle('bridge:request', async (event, route, body) => {
    guard(event);
    if (setup.running && body !== undefined) return { ok: false, message: '正在安装后台服务，请稍候。', code: 'busy' };
    try { return { ok: true, data: await backend.call(route, body) }; }
    catch (error) { return errorValue(error); }
  });
  ipcMain.handle('bridge:copy', (event, text) => {
    guard(event);
    if (typeof text !== 'string' || text.length > 4096) return false;
    clipboard.writeText(text); return true;
  });
  ipcMain.on('bridge:workspace', (event, state) => { if (trusted(event)) { dirty = state?.dirty === true; busy = state?.busy === true; } });
  ipcMain.handle('bridge:export', async (event, kind) => {
    guard(event);
    const choices = { config: ['/api/claude-config', 'modelbridge-claude-config.json'], diagnostics: ['/api/diagnostics', 'modelbridge-diagnostics.json'] };
    if (typeof kind !== 'string' || !Object.hasOwn(choices, kind)) return { ok: false, message: '不支持的导出类型。' };
    try {
      const [route, filename] = choices[kind];
      const data = await backend.call(route);
      const selected = await dialog.showSaveDialog(win, { title: kind === 'config' ? '导出 Claude 配置' : '导出诊断摘要', defaultPath: path.join(app.getPath('downloads'), filename), filters: [{ name: 'JSON 文件', extensions: ['json'] }] });
      if (selected.canceled || !selected.filePath) return { ok: true, canceled: true };
      await fs.writeFile(selected.filePath, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
      return { ok: true, canceled: false };
    } catch (error) { return errorValue(error); }
  });
}
function installMenu() {
  const navigate = (label, accelerator, panel) => ({ label, accelerator, click: () => show(panel) });
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'ModelBridge', submenu: [{ label: '关于 ModelBridge', click: () => app.showAboutPanel() }, { type: 'separator' },
      navigate('模型连接…', 'CmdOrCtrl+,', 'connection'), { type: 'separator' }, { role: 'hide', label: '隐藏 ModelBridge' },
      { role: 'hideOthers', label: '隐藏其他' }, { role: 'unhide', label: '显示全部' }, { type: 'separator' },
      { label: '退出 ModelBridge', accelerator: 'CmdOrCtrl+Q', click: () => app.quit() }] },
    { label: '编辑', submenu: [{ role: 'undo', label: '撤销' }, { role: 'redo', label: '重做' }, { type: 'separator' },
      { role: 'cut', label: '剪切' }, { role: 'copy', label: '复制' }, { role: 'paste', label: '粘贴' }, { role: 'selectAll', label: '全选' }] },
    { label: '显示', submenu: [navigate('模型连接', 'CmdOrCtrl+1', 'connection'), navigate('接入 Claude', 'CmdOrCtrl+2', 'claude'),
      navigate('状态与帮助', 'CmdOrCtrl+3', 'help'), { type: 'separator' }, navigate('刷新连接状态', 'CmdOrCtrl+R', 'refresh'),
      { type: 'separator' }, { role: 'resetZoom', label: '实际大小' }, { role: 'zoomIn', label: '放大' }, { role: 'zoomOut', label: '缩小' }, { role: 'togglefullscreen', label: '进入全屏' }] },
    { label: '窗口', submenu: [{ role: 'minimize', label: '最小化' }, { role: 'zoom', label: '缩放' }, { label: '关闭窗口', accelerator: 'CmdOrCtrl+W', click: () => win?.hide() }, { type: 'separator' }, { label: '显示主窗口', click: () => show() }] },
    { label: '帮助', submenu: [{ label: 'ModelBridge 帮助', click: () => show('help') }] }
  ]));
}
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();
else {
  app.on('second-instance', () => show());
  app.on('activate', () => show());
  app.on('window-all-closed', () => {});
  app.on('before-quit', event => {
    if (quitting) return;
    if (setup.running) {
      event.preventDefault();
      if (!confirmingQuit) {
        confirmingQuit = true;
        dialog.showMessageBox(win, { message: '正在安装后台服务', detail: '请等待安装完成。你可以关闭窗口，安装会继续。', buttons: ['继续安装'] }).finally(() => { confirmingQuit = false; });
      }
      return;
    }
    if (!dirty && !busy) { quitting = true; clearInterval(poll); return; }
    event.preventDefault();
    if (confirmingQuit) return;
    confirmingQuit = true;
    dialog.showMessageBox(win, { type: 'question', buttons: ['继续使用', '退出客户端'], defaultId: 0, cancelId: 0,
      message: '有未保存的更改或正在验证的连接', detail: '退出会丢弃窗口中的草稿。后台模型服务会继续运行；已提交的验证可能仍在后台进行。' }).then(result => {
      confirmingQuit = false;
      if (result.response === 1) { quitting = true; clearInterval(poll); app.quit(); }
    });
  });
  app.whenReady().then(() => {
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
    protocol.handle('modelbridge', async request => {
      const url = new URL(request.url);
      if (url.host !== 'app' || !Object.hasOwn(ASSETS, url.pathname)) return new Response('Not found', { status: 404 });
      return new Response(await fs.readFile(path.join(WEB, url.pathname.slice(1))), { headers: { 'Content-Type': ASSETS[url.pathname], 'Content-Security-Policy': CSP, 'Cache-Control': 'no-store' } });
    });
    app.setAboutPanelOptions({ applicationName: 'ModelBridge', applicationVersion: app.getVersion(), version: 'macOS 桌面预览版', copyright: '第三方模型连接工具 · 非 Anthropic 官方产品', iconPath: path.join(__dirname, '..', 'macos', 'ModelBridge.icns') });
    installIPC(); installMenu(); createWindow();
    const icon = nativeImage.createFromPath(path.join(__dirname, 'trayTemplate.png'));
    icon.setTemplateImage(true); tray = new Tray(icon);
    tray.on('click', () => show()); refreshTray();
    pollStatus(); poll = setInterval(pollStatus, 15000);
  }).catch(() => { dialog.showErrorBox('ModelBridge', '客户端启动失败，请重新打开。'); quitting = true; app.quit(); });
}
