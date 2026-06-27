/**
 * G-Mini Agent — WebSocket Client
 * Conexión Socket.IO al backend Python.
 */

// Socket.IO se carga desde node_modules via require en Electron
// pero en un contexto sin nodeIntegration, lo cargamos desde un bundle.
// Para la versión inicial, incluimos io inline desde CDN en index.html
// o lo resolvemos aquí via el path de node_modules.

class GminiWebSocket {
    constructor() {
        this.socket = null;
        this.backendUrl = 'http://127.0.0.1:8765';
        this.connected = false;
        this.listeners = {};
        this.reconnectTimer = null;
    }

    async connect() {
        try {
            // Obtener URL del backend via preload
            if (window.gmini) {
                this.backendUrl = await window.gmini.getBackendUrl();
            }

            // socket.io-client se carga via <script> tag en index.html (global `io`)
            if (typeof io === 'undefined') {
                throw new Error('socket.io-client no cargado. Verifica que el script esté incluido en index.html');
            }

            this.socket = io(this.backendUrl, {
                transports: ['websocket', 'polling'],
                reconnection: true,
                reconnectionAttempts: Infinity,
                reconnectionDelay: 2000,
                reconnectionDelayMax: 10000,
                timeout: 10000,
            });

            this._setupEventHandlers();
            return true;
        } catch (error) {
            console.error('Error conectando WebSocket:', error);
            this._emit('error', { message: error.message });
            return false;
        }
    }

    _setupEventHandlers() {
        this.socket.on('connect', () => {
            console.log('WebSocket conectado');
            this.connected = true;
            this._emit('connected');
        });

        this.socket.on('disconnect', (reason) => {
            console.log('WebSocket desconectado:', reason);
            this.connected = false;
            this._emit('disconnected', { reason });
        });

        this.socket.on('connect_error', (error) => {
            console.warn('Error de conexión WS:', error.message);
            this.connected = false;
            this._emit('error', { message: error.message });
        });

        // Agent events
        this.socket.on('agent:message', (data) => {
            this._emit('agent:message', data);
        });

        this.socket.on('agent:status', (data) => {
            this._emit('agent:status', data);
        });

        this.socket.on('agent:screenshot', (data) => {
            this._emit('agent:screenshot', data);
        });

        this.socket.on('agent:media', (data) => {
            this._emit('agent:media', data);
        });

        this.socket.on('agent:audio', (data) => {
            this._emit('agent:audio', data);
        });

        this.socket.on('agent:speak', (data) => {
            this._emit('agent:speak', data);
        });

        this.socket.on('agent:audio_interrupt', (data) => {
            this._emit('agent:audio_interrupt', data);
        });

        this.socket.on('agent:lipsync', (data) => {
            this._emit('agent:lipsync', data);
        });

        this.socket.on('agent:emotion', (data) => {
            this._emit('agent:emotion', data);
        });

        this.socket.on('agent:stt_result', (data) => {
            this._emit('agent:stt_result', data);
        });

        this.socket.on('config:updated', (data) => {
            this._emit('config:updated', data);
        });

        // Action visualization events
        this.socket.on('agent:action', (data) => {
            this._emit('agent:action', data);
        });

        this.socket.on('agent:action_result', (data) => {
            this._emit('agent:action_result', data);
        });

        this.socket.on('agent:executing', (data) => {
            this._emit('agent:executing', data);
        });

        this.socket.on('agent:approval', (data) => {
            this._emit('agent:approval', data);
        });

        this.socket.on('agent:subagents', (data) => {
            this._emit('agent:subagents', data);
        });

        this.socket.on('gateway:notification', (data) => {
            this._emit('gateway:notification', data);
        });

        // Node management events (Phase 7)
        this.socket.on('node:paired', (data) => {
            this._emit('node:paired', data);
        });
        this.socket.on('node:pair_error', (data) => {
            this._emit('node:pair_error', data);
        });
        this.socket.on('node:reconnected', (data) => {
            this._emit('node:reconnected', data);
        });
        this.socket.on('node:removed', (data) => {
            this._emit('node:removed', data);
        });
        this.socket.on('node:banned', (data) => {
            this._emit('node:banned', data);
        });
        this.socket.on('agent:node_update', (data) => {
            this._emit('agent:node_update', data);
        });
        this.socket.on('agent:node_removed', (data) => {
            this._emit('agent:node_removed', data);
        });

        // Canvas events (Phase 8)
        this.socket.on('canvas:snapshot', (data) => {
            this._emit('canvas:snapshot', data);
        });
        this.socket.on('canvas:created', (data) => {
            this._emit('canvas:created', data);
        });
        this.socket.on('canvas:updated', (data) => {
            this._emit('canvas:updated', data);
        });
        this.socket.on('canvas:deleted', (data) => {
            this._emit('canvas:deleted', data);
        });

        // Session restore on reconnect
        this.socket.on('session:restored', (data) => {
            this._emit('session:restored', data);
        });

        // Realtime voice availability
        this.socket.on('agent:realtime_available', (data) => {
            this._emit('agent:realtime_available', data);
        });

        // Realtime user speech transcription
        this.socket.on('agent:realtime_user_text', (data) => {
            this._emit('agent:realtime_user_text', data);
        });
    }

    // ── Enviar eventos ────────────────────────────────

    sendMessage(text, attachments = []) {
        if (!this.connected) return;
        this.socket.emit('user:message', { text, attachments });
    }

    sendCommand(action, payload = {}) {
        if (!this.connected) return;
        this.socket.emit('user:command', { action, ...payload });
    }

    sendConfig(section, key, value) {
        if (!this.connected) return;
        this.socket.emit('user:config', { section, key, value });
    }

    sendAudio(audioData) {
        if (!this.connected) return;
        this.socket.emit('user:stt_audio', { audio: audioData });
    }

    startRealtimeVoice(provider = 'openai', voice = '', mode = 'native') {
        if (!this.connected) return;
        this.socket.emit('user:realtime_start', { provider, voice, mode });
    }

    sendRealtimeAudio(audioB64) {
        if (!this.connected) return;
        this.socket.emit('user:realtime_audio', { audio: audioB64 });
    }

    stopRealtimeVoice() {
        if (!this.connected) return;
        this.socket.emit('user:realtime_stop', {});
    }

    toggleScreenStream(enable = true) {
        if (!this.connected) return;
        this.socket.emit('user:screen_stream_toggle', { enable });
    }

    checkRealtimeAvailable(provider = '', model = '') {
        if (!this.connected) return;
        this.socket.emit('user:check_realtime', { provider, model });
    }

    // ── Event system ──────────────────────────────────

    on(event, callback) {
        if (!this.listeners[event]) {
            this.listeners[event] = [];
        }
        this.listeners[event].push(callback);
    }

    off(event, callback) {
        if (!this.listeners[event]) return;
        this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
    }

    _emit(event, data = null) {
        const handlers = this.listeners[event];
        if (handlers) {
            handlers.forEach(cb => cb(data));
        }
    }

    disconnect() {
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }
        this.connected = false;
    }
}

// Instancia global
const ws = new GminiWebSocket();
