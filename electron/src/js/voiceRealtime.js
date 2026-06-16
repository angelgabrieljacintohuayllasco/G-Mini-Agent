/**
 * G-Mini Agent — Voice Realtime Module
 * Captura de micrófono PCM16 a 16 kHz y playback de audio streaming
 * para conversación bidireccional con proveedores RT (OpenAI / Google / xAI).
 */

// eslint-disable-next-line no-unused-vars
class VoiceRealtime {
    constructor() {
        /** @type {MediaStream|null} */
        this._stream = null;
        /** @type {AudioContext|null} */
        this._captureCtx = null;
        /** @type {ScriptProcessorNode|null} */
        this._processor = null;
        /** @type {AudioContext|null} */
        this._playbackCtx = null;
        /** @type {AnalyserNode|null} */
        this._playbackAnalyser = null;
        /** @type {Uint8Array|null} */
        this._analyserBuffer = null;
        /** @type {Array<AudioBuffer>} */
        this._playQueue = [];
        this._playing = false;
        this._active = false;
        this._provider = 'openai';
        this._mode = 'native';  // 'native' | 'simulated'

        // Capture config — PCM16 mono 16 kHz
        this._sampleRate = 16000;
        this._bufferSize = 4096;
    }

    /**
     * Inicia la captura de micrófono y la sesión RT en el backend.
     * @param {string} provider - 'openai' | 'google' | 'xai' | nombre del provider de texto
     * @param {string} voice - Nombre de la voz (ej: 'Aoede', 'Kore')
     * @param {string} mode - 'native' | 'simulated'
     * @returns {Promise<boolean>}
     */
    async start(provider = 'openai', voice = '', mode = 'native') {
        if (this._active) {
            await this.stop();
        }

        this._provider = provider;
        this._mode = mode;

        try {
            this._stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    sampleRate: this._sampleRate,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });
        } catch (err) {
            console.error('[VoiceRealtime] Mic access error:', err);
            return false;
        }

        try {
            this._captureCtx = new AudioContext({ sampleRate: this._sampleRate });
            const source = this._captureCtx.createMediaStreamSource(this._stream);

            // ScriptProcessorNode for PCM capture (simple, works in Electron Chromium)
            this._processor = this._captureCtx.createScriptProcessor(this._bufferSize, 1, 1);
            this._processor.onaudioprocess = (ev) => {
                if (!this._active) return;
                const float32 = ev.inputBuffer.getChannelData(0);
                const pcm16 = this._float32ToPcm16(float32);
                const b64 = this._arrayBufferToBase64(pcm16.buffer);
                ws.sendRealtimeAudio(b64);
            };

            source.connect(this._processor);
            this._processor.connect(this._captureCtx.destination);

            // Playback context
            this._playbackCtx = new AudioContext({ sampleRate: 24000 });
            this._playbackAnalyser = this._playbackCtx.createAnalyser();
            this._playbackAnalyser.fftSize = 256;
            this._playbackAnalyser.smoothingTimeConstant = 0.6;
            this._analyserBuffer = new Uint8Array(this._playbackAnalyser.fftSize);
            this._playQueue = [];
            this._playing = false;

            this._active = true;

            // Tell backend to start RT session
            ws.startRealtimeVoice(this._provider, voice, this._mode);

            console.log(`[VoiceRealtime] Started — provider=${provider}`);
            return true;
        } catch (err) {
            console.error('[VoiceRealtime] Setup error:', err);
            this._cleanup();
            return false;
        }
    }

    /**
     * Detiene la sesión de voz en tiempo real.
     */
    async stop() {
        if (!this._active) return;
        this._active = false;

        // Tell backend to stop RT session
        ws.stopRealtimeVoice();

        this._cleanup();
        console.log('[VoiceRealtime] Stopped');
    }

    /**
     * Recibe un chunk de audio del agente (PCM16, base64) y lo encola para playback.
     * @param {string} audioB64 - PCM16 data en base64
     * @param {string} format - 'pcm16' (default)
     * @returns {number} Duración estimada en ms del chunk (para lipsync hints)
     */
    playAudioChunk(audioB64, format = 'pcm16') {
        if (!this._playbackCtx) return 0;

        try {
            const raw = atob(audioB64);
            const bytes = new Uint8Array(raw.length);
            for (let i = 0; i < raw.length; i++) {
                bytes[i] = raw.charCodeAt(i);
            }

            let float32;
            if (format === 'pcm16') {
                float32 = this._pcm16ToFloat32(new Int16Array(bytes.buffer));
            } else {
                // fallback: assume float32 directly
                float32 = new Float32Array(bytes.buffer);
            }

            // PCM16 from backend is always 24kHz — use source rate, not context rate.
            // AudioContext will resample automatically during playback.
            const sampleRate = (format === 'pcm16') ? 24000 : this._playbackCtx.sampleRate;
            const audioBuffer = this._playbackCtx.createBuffer(1, float32.length, sampleRate);
            audioBuffer.getChannelData(0).set(float32);

            this._playQueue.push(audioBuffer);
            this._drainPlayQueue();

            // Return estimated duration in ms for lipsync
            return (float32.length / sampleRate) * 1000;
        } catch (err) {
            console.error('[VoiceRealtime] Playback error:', err);
            return 0;
        }
    }

    // ── Private ───────────────────────────────────

    _drainPlayQueue() {
        if (this._playing || this._playQueue.length === 0 || !this._playbackCtx) return;
        this._playing = true;

        const buffer = this._playQueue.shift();
        const source = this._playbackCtx.createBufferSource();
        source.buffer = buffer;
        if (this._playbackAnalyser) {
            source.connect(this._playbackAnalyser);
            this._playbackAnalyser.connect(this._playbackCtx.destination);
        } else {
            source.connect(this._playbackCtx.destination);
        }
        source.onended = () => {
            this._playing = false;
            this._drainPlayQueue();
        };
        source.start();
    }

    _cleanup() {
        if (this._processor) {
            this._processor.onaudioprocess = null;
            try { this._processor.disconnect(); } catch (_) { /* noop */ }
            this._processor = null;
        }
        if (this._captureCtx) {
            try { this._captureCtx.close(); } catch (_) { /* noop */ }
            this._captureCtx = null;
        }
        if (this._stream) {
            this._stream.getTracks().forEach((t) => t.stop());
            this._stream = null;
        }
        if (this._playbackCtx) {
            try { this._playbackCtx.close(); } catch (_) { /* noop */ }
            this._playbackCtx = null;
        }
        this._playbackAnalyser = null;
        this._analyserBuffer = null;
        this._playQueue = [];
        this._playing = false;
    }

    /**
     * Nivel de amplitud (RMS) del audio que se esta reproduciendo, 0..1.
     * Usado para animar la boca del avatar en modo skin.
     * @returns {number}
     */
    getLevel() {
        if (!this._playbackAnalyser || !this._analyserBuffer) return 0;
        this._playbackAnalyser.getByteTimeDomainData(this._analyserBuffer);
        let sumSquares = 0;
        for (let i = 0; i < this._analyserBuffer.length; i++) {
            const norm = (this._analyserBuffer[i] - 128) / 128;
            sumSquares += norm * norm;
        }
        const rms = Math.sqrt(sumSquares / this._analyserBuffer.length);
        return Math.min(1, rms * 4);
    }

    /**
     * Convierte Float32Array (-1..1) a Int16Array PCM16.
     */
    _float32ToPcm16(float32) {
        const pcm16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return pcm16;
    }

    /**
     * Convierte Int16Array PCM16 a Float32Array (-1..1).
     */
    _pcm16ToFloat32(pcm16) {
        const float32 = new Float32Array(pcm16.length);
        for (let i = 0; i < pcm16.length; i++) {
            float32[i] = pcm16[i] / (pcm16[i] < 0 ? 0x8000 : 0x7FFF);
        }
        return float32;
    }

    /**
     * ArrayBuffer → base64 string.
     */
    _arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    /** @returns {boolean} */
    get active() {
        return this._active;
    }

    /** @returns {string} */
    get provider() {
        return this._provider;
    }
}

// Instancia global
const voiceRealtime = new VoiceRealtime();
