/**
 * G-Mini Agent — Preload de la ventana de preparación (primer arranque).
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('gminiSetup', {
    getState: () => ipcRenderer.invoke('setup:get-state'),
    onState: (callback) => {
        const listener = (_event, state) => callback(state);
        ipcRenderer.on('setup:state', listener);
        return () => ipcRenderer.removeListener('setup:state', listener);
    },
    retry: () => ipcRenderer.invoke('setup:retry'),
    openLog: () => ipcRenderer.invoke('setup:open-log'),
    quit: () => ipcRenderer.invoke('setup:quit'),
});
