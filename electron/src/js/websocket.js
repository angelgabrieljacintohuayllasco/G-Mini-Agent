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

            const token = window.gminiAuth ? await window.gminiAuth.ready : '';

            this.socket = io(this.backendUrl, {
                auth: { token },
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
            // socket.io no reintenta solo cuando el servidor cierra la sesión (S8).
            if (reason === 'io server disconnect') {
                setTimeout(() => this.socket?.connect(), 2000);
            }
        });

        this.socket.on('connect_error', (error) => {
            console.warn('Error de conexión WS:', error.message);
            this.connected = false;
            this._emit('error', { message: error.message });
        });

        // B8: se reenvía cualquier evento del backend. La lista fija de antes
        // perdía agent:realtime_ready, agent:screen_stream_status, config:error
        // y crew:update / crew:finished.
        this.socket.onAny((event, data) => {
            this._emit(event, data);
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
        if (!handlers) return;
        // Un manejador que falla no debe impedir que corran los demás.
        for (const cb of handlers.slice()) {
            try {
                cb(data);
            } catch (err) {
                console.error(`[WS] Error en el manejador de ${event}:`, err);
            }
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
