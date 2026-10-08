const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('proctor', {
  request: (command, payload) => ipcRenderer.invoke('proctor-command', command, payload),
  onGuard: (callback) => {
    const handler = (_event, state) => callback(state);
    ipcRenderer.on('guard-state', handler);
    return () => ipcRenderer.removeListener('guard-state', handler);
  }
});
