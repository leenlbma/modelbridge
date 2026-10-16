'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('modelBridgeDesktop', {
  platform: process.platform,
  version: process.argv.find(value => value.startsWith('--modelbridge-version='))?.split('=')[1] || '',
  setupState: () => ipcRenderer.invoke('bridge:setup-state'),
  installService: () => ipcRenderer.invoke('bridge:setup-install'),
  stopService: () => ipcRenderer.invoke('bridge:setup-stop'),
  onSetupState: callback => ipcRenderer.on('bridge:setup-state', (_event, value) => callback(value)),
  request: (route, body) => ipcRenderer.invoke('bridge:request', route, body),
  exportJSON: kind => ipcRenderer.invoke('bridge:export', kind),
  copyText: text => ipcRenderer.invoke('bridge:copy', text),
  workspaceState: value => ipcRenderer.send('bridge:workspace', { dirty: value?.dirty === true, busy: value?.busy === true }),
  onNavigate: callback => ipcRenderer.on('bridge:navigate', (_event, panel) => {
    if (['connection', 'claude', 'help', 'refresh'].includes(panel)) callback(panel);
  })
});
