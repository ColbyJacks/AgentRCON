const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // Python HTTP REST API endpoints
  getStatus: () => fetch('http://127.0.0.1:8000/api/status').then(res => res.json()),
  getLogs: () => fetch('http://127.0.0.1:8000/api/logs').then(res => res.json()),
  getHistory: () => fetch('http://127.0.0.1:8000/api/history').then(res => res.json()),
  controlServer: (action) => fetch('http://127.0.0.1:8000/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action })
  }).then(res => res.json()),
  runCommand: (command) => fetch('http://127.0.0.1:8000/api/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command })
  }).then(res => res.json()),
  searchItem: (query) => fetch('http://127.0.0.1:8000/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  }).then(res => res.json()),

  // Electron native IPC channels
  selectServerDir: () => ipcRenderer.invoke('select-server-dir'),
  getConfig: () => ipcRenderer.invoke('get-config'),
  saveConfig: (cfg) => ipcRenderer.invoke('save-config', cfg)
});
