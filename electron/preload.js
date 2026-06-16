/**
 * G-Mini Agent — Preload script.
 * Expone APIs seguras al renderer via contextBridge.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('gmini', {
    // Backend URL
    getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),

    // Guardar media generada (imagen/video/audio) en carpeta a eleccion del usuario
    saveMediaAs: (url, filename) => ipcRenderer.invoke('save-media-as', url, filename),

    // Window controls
    minimize: () => ipcRenderer.invoke('minimize-window'),
    close: () => ipcRenderer.invoke('close-window'),
    toggleAlwaysOnTop: (value) => ipcRenderer.invoke('toggle-always-on-top', value),
    getAppRuntimeSettings: () => ipcRenderer.invoke('get-app-runtime-settings'),
    reloadAppRuntimeSettings: () => ipcRenderer.invoke('reload-app-runtime-settings'),

    // Overlay
    toggleOverlay: (enable) => ipcRenderer.invoke('toggle-overlay', enable),
    setOverlayText: (text) => ipcRenderer.invoke('set-overlay-text', text),
    overlayGetState: () => ipcRenderer.invoke('overlay:get-state'),
    overlaySetInteractive: (interactive) => ipcRenderer.invoke('overlay:set-interactive', interactive),
    overlaySetCharacterRuntime: (payload) => ipcRenderer.invoke('overlay:set-character-runtime', payload),
    overlayMoveBy: (dx, dy) => ipcRenderer.invoke('overlay:move-by', dx, dy),
    overlayResizeBy: (delta, anchor) => ipcRenderer.invoke('overlay:resize-by', delta, anchor),
    overlaySetBounds: (bounds) => ipcRenderer.invoke('overlay:set-bounds', bounds),

    // Overlay receive
    onOverlayText: (callback) => {
        ipcRenderer.on('overlay-text', (_, text) => callback(text));
    },
    onOverlayState: (callback) => {
        ipcRenderer.on('overlay-state', (_, state) => callback(state));
    },
    onOverlayCharacterRuntime: (callback) => {
        ipcRenderer.on('overlay-character-runtime', (_, payload) => callback(payload));
    },

    // Skin (avatar flotante)
    skinSetMode: (mode) => ipcRenderer.invoke('skin:set-mode', mode),
    skinGetState: () => ipcRenderer.invoke('skin:get-state'),
    skinList: () => ipcRenderer.invoke('skin:list'),
    skinPickFile: (kind) => ipcRenderer.invoke('skin:pick-file', kind),
    skinCreate: (payload) => ipcRenderer.invoke('skin:create', payload),
    skinSetInteractive: (interactive) => ipcRenderer.invoke('skin:set-interactive', interactive),
    skinMoveBy: (dx, dy) => ipcRenderer.invoke('skin:move-by', dx, dy),
    skinResizeBy: (delta) => ipcRenderer.invoke('skin:resize-by', delta),
    skinSetBounds: (bounds) => ipcRenderer.invoke('skin:set-bounds', bounds),
    skinMinimize: () => ipcRenderer.invoke('skin:minimize'),
    onSkinState: (callback) => {
        ipcRenderer.on('skin-state', (_, state) => callback(state));
    },

    // Skin: mini-chat burbuja
    skinChatOpen: () => ipcRenderer.invoke('skin:chat-open'),
    skinChatClose: () => ipcRenderer.invoke('skin:chat-close'),
    skinChatSend: (text) => ipcRenderer.invoke('skin:chat-send', text),
    skinChatRelay: (payload) => ipcRenderer.invoke('skin:chat-relay', payload),
    onSkinChatSend: (callback) => {
        ipcRenderer.on('skin-chat-send', (_, text) => callback(text));
    },
    onSkinChatRelay: (callback) => {
        ipcRenderer.on('skin-chat-relay', (_, payload) => callback(payload));
    },

    // Skin: boton mic (proxy hacia voz en tiempo real de mainWindow)
    skinVoiceToggle: () => ipcRenderer.invoke('skin:voice-toggle'),
    skinVoiceState: (payload) => ipcRenderer.invoke('skin:voice-state', payload),
    onSkinVoiceToggle: (callback) => {
        ipcRenderer.on('skin-voice-toggle', () => callback());
    },
    onSkinVoiceState: (callback) => {
        ipcRenderer.on('skin-voice-state', (_, payload) => callback(payload));
    },

    // Window transparency & effects
    setWindowOpacity: (opacity) => ipcRenderer.invoke('set-window-opacity', opacity),
    showClickIndicator: (x, y, type) => ipcRenderer.invoke('show-click-indicator', x, y, type),
    showScreenshotOverlay: () => ipcRenderer.invoke('show-screenshot-overlay'),
    showCursorBubble: (x, y) => ipcRenderer.invoke('show-cursor-bubble', x, y),
    setExecutingMode: (active) => ipcRenderer.invoke('set-executing-mode', active),
});
