/**
 * G-Mini Agent — Reproductor de la voz del agente (TTS del núcleo).
 *
 * `agent:audio` sin `stream` trae un clip completo (wav o mp3 en base64).
 * Antes solo sonaba en modo avatar (B3); ahora se encola y suena también en
 * el chat, un clip detrás de otro, salvo que el usuario lo silencie. La
 * muestra de voces de Configuración usa el mismo reproductor y suena aunque
 * esté en silencio, porque la pidió el usuario.
 */
(function () {
    'use strict';

    const MUTE_KEY = 'gmini_tts_muted';
    const listeners = new Set();
    const queue = [];
    let ctx = null;
    let analyser = null;
    let levelBuffer = null;
    let current = null;
    let decoding = Promise.resolve();
    let muted = readMuted();

    function readMuted() {
        try { return localStorage.getItem(MUTE_KEY) === '1'; } catch (e) { return false; }
    }

    function ensureContext() {
        if (!ctx || ctx.state === 'closed') {
            ctx = new AudioContext();
            analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            analyser.smoothingTimeConstant = 0.6;
            analyser.connect(ctx.destination);
            levelBuffer = new Uint8Array(analyser.fftSize);
        }
        if (ctx.state === 'suspended') ctx.resume().catch(() => {});
        return ctx;
    }

    function base64ToArrayBuffer(b64) {
        const raw = atob(b64);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
        return bytes.buffer;
    }

    // decodeAudioData reconoce el contenedor: sirve para wav, mp3 u ogg.
    function decode(b64) {
        return ensureContext().decodeAudioData(base64ToArrayBuffer(b64));
    }

    function state() {
        return { muted, playing: !!current, preview: !!(current && current.preview), queued: queue.length };
    }

    function emit() {
        const snapshot = state();
        listeners.forEach((cb) => {
            try { cb(snapshot); } catch (err) { console.warn('[Voz] Error en un oyente del reproductor:', err); }
        });
    }

    function startSource(buffer, preview) {
        const source = ensureContext().createBufferSource();
        source.buffer = buffer;
        source.connect(analyser);
        current = { source, preview };
        source.onended = () => {
            if (current && current.source === source) current = null;
            if (preview) emit();
            else playNext();
        };
        source.start();
        emit();
        return source;
    }

    function playNext() {
        if (current) return;
        const buffer = queue.shift();
        if (buffer) startSource(buffer, false);
        else emit();
    }

    function stop() {
        queue.length = 0;
        if (current) {
            const { source } = current;
            current = null;
            try {
                source.onended = null;
                source.stop();
            } catch (e) { /* ya terminó */ }
        }
        emit();
    }

    window.gminiTtsPlayer = {
        /** Encola un clip del agente. Resuelve false si está en silencio o no se pudo decodificar. */
        enqueue(b64) {
            if (muted || !b64) return Promise.resolve(false);
            // Se decodifica en orden: un clip largo no se adelanta a uno corto.
            const job = decoding
                .then(() => decode(b64))
                .then((buffer) => {
                    if (muted) return false;
                    queue.push(buffer);
                    playNext();
                    return true;
                })
                .catch((err) => {
                    console.warn('[Voz] No se pudo reproducir el audio del agente:', err);
                    return false;
                });
            decoding = job.then(() => undefined);
            return job;
        },

        /** Muestra de una voz: corta lo que suene y se oye aunque esté en silencio. */
        async preview(b64) {
            const buffer = await decode(b64);
            stop();
            const source = startSource(buffer, true);
            return new Promise((resolve) => {
                source.addEventListener('ended', () => resolve(true), { once: true });
            });
        },

        stop,

        get muted() {
            return muted;
        },

        setMuted(value) {
            muted = !!value;
            try { localStorage.setItem(MUTE_KEY, muted ? '1' : '0'); } catch (e) { /* sin storage */ }
            if (muted) stop();
            else emit();
        },

        state,

        /** Amplitud (RMS) de lo que suena, 0..1: mueve la boca del avatar. */
        level() {
            if (!current || !analyser || !levelBuffer) return 0;
            analyser.getByteTimeDomainData(levelBuffer);
            let sum = 0;
            for (let i = 0; i < levelBuffer.length; i += 1) {
                const norm = (levelBuffer[i] - 128) / 128;
                sum += norm * norm;
            }
            return Math.min(1, Math.sqrt(sum / levelBuffer.length) * 4);
        },

        onChange(cb) {
            listeners.add(cb);
            return () => listeners.delete(cb);
        },
    };
})();
