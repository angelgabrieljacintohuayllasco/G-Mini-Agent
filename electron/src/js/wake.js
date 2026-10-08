/**
 * G-Mini Agent — Manos libres: "Oye G-Mini" abre la voz.
 *
 * Con voice.wake_word.enabled el micrófono queda abierto en modo solo-escucha:
 * no abre ninguna sesión, solo manda trozos PCM16 de 16 kHz mono (128 ms) por
 * Socket.IO como user:wake_audio. El núcleo responde agent:wake {phrase,
 * command, transcript} cuando oye la palabra de activación: se deja de
 * escuchar, el orbe avisa, se abre la conversación por voz como si se pulsara
 * su botón y, si después del nombre venía un pedido, se envía como mensaje.
 * Mientras la voz en tiempo real tiene el micrófono, esto queda en pausa y
 * vuelve solo cuando termina.
 *
 * El permiso del micrófono se pide al activar el modo. Indicadores: el botón
 * de la barra y la etiqueta "Micrófono abierto" junto a los chips de estado.
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const RATE = 16000;
    const CHUNK = 2048;          // 128 ms a 16 kHz: el núcleo pide trozos de 100 a 200 ms
    const HEARD_MS = 1800;

    const state = { enabled: false, listening: false, starting: false, error: '', token: 0 };
    const capture = { stream: null, ctx: null, source: null, processor: null };
    const $ = (id) => document.getElementById(id);
    const agentName = () => window.gminiIdentity?.name?.() || 'G-Mini';
    const phrase = () => `«Oye ${agentName()}»`;
    const hasMic = !!navigator.mediaDevices?.getUserMedia;

    // La voz en tiempo real tiene el micrófono (o lo está pidiendo): manos libres en pausa.
    const voiceBusy = () => typeof voiceRealtime !== 'undefined' && (voiceRealtime.active || voiceRealtime.starting);
    const shouldListen = () => state.enabled && !state.error && !voiceBusy();

    // ── Captura solo-escucha ───────────────────────────────────

    async function startCapture() {
        if (state.listening || state.starting) return true;
        state.starting = true;
        const token = ++state.token;
        render();
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, sampleRate: RATE, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            });
        } catch (err) {
            if (token === state.token) {
                state.starting = false;
                state.error = err?.name === 'NotAllowedError'
                    ? 'Windows o la app no dieron permiso para usar el micrófono.'
                    : `No se pudo abrir el micrófono (${err?.message || err?.name || 'error'}).`;
                render();
            }
            return false;
        }
        // Se apagó o empezó la voz mientras se esperaba el permiso.
        if (token !== state.token || !shouldListen()) {
            stream.getTracks().forEach((track) => track.stop());
            if (token === state.token) state.starting = false;
            render();
            return token === state.token;
        }
        try {
            capture.stream = stream;
            capture.ctx = new AudioContext({ sampleRate: RATE });
            capture.source = capture.ctx.createMediaStreamSource(stream);
            const pcm = await window.gminiPcmCapture.attach(capture.ctx, capture.source, {
                chunkSize: CHUNK,
                onChunk: (pcm16) => {
                    if (!state.listening || typeof ws === 'undefined' || !ws.connected) return;
                    ws.sendWakeAudio(window.gminiPcmCapture.toBase64(pcm16));
                },
            });
            // Se apagó o empezó la voz mientras cargaba el worklet: stopCapture() ya soltó todo.
            if (token !== state.token) {
                pcm.stop();
                return true;
            }
            capture.processor = pcm;
            // Si el usuario sale del micrófono desde Windows, el modo no se queda colgado.
            stream.getAudioTracks()[0]?.addEventListener('ended', () => {
                if (capture.stream !== stream) return;
                stopCapture();
                state.error = 'El micrófono se desconectó.';
                render();
            });
            state.listening = true;
            state.starting = false;
            state.error = '';
            render();
            return true;
        } catch (err) {
            state.starting = false;
            stopCapture();
            state.error = `No se pudo preparar el audio (${err?.message || 'error'}).`;
            render();
            return false;
        }
    }

    function stopCapture() {
        state.token += 1;
        state.starting = false;
        state.listening = false;
        capture.processor?.stop();
        try { capture.source?.disconnect(); } catch (e) { /* ya desconectado */ }
        capture.stream?.getTracks().forEach((track) => track.stop());
        capture.ctx?.close().catch(() => {});
        capture.stream = capture.ctx = capture.source = capture.processor = null;
        render();
    }

    /** Escucha si corresponde y suelta el micrófono si no. */
    function sync() {
        if (shouldListen()) {
            if (!state.listening && !state.starting) startCapture();
        } else if (state.listening || state.starting) {
            stopCapture();
        } else {
            render();
        }
    }

    // ── Activar y desactivar ───────────────────────────────────

    async function saveEnabled(on) {
        try {
            // wake_word es un objeto: se conservan las frases propias.
            const resp = await fetch(`${API}/config/voice`);
            const current = (await resp.json())?.data?.voice?.wake_word;
            const value = { ...(current && typeof current === 'object' ? current : {}), enabled: on };
            return await window.settingsManager._saveConfigValue('voice', 'wake_word', value);
        } catch (err) {
            return false;
        }
    }

    /** Desde el botón de la barra, la paleta o Configuración > Voz. */
    async function setEnabled(on) {
        if (on === state.enabled && !state.error) return true;
        if (on && !hasMic) {
            state.error = 'Este equipo no ofrece un micrófono.';
            render();
            return false;
        }
        state.error = '';
        if (on) {
            state.enabled = true;
            // El permiso se pide ahora, al activar; con la voz abierta ya está dado.
            if (!voiceBusy() && !(await startCapture())) {
                state.enabled = false;
                render();
                return false;
            }
        } else {
            state.enabled = false;
            stopCapture();
        }
        render();
        if (!(await saveEnabled(on))) {
            if (typeof chatManager !== 'undefined') chatManager._toast('No se pudo guardar el modo manos libres en la configuración.', true);
        }
        return true;
    }

    async function loadFromConfig() {
        try {
            const resp = await fetch(`${API}/config/voice`);
            if (!resp.ok) return;
            const enabled = !!(await resp.json())?.data?.voice?.wake_word?.enabled;
            if (enabled === state.enabled) return;
            state.enabled = enabled;
            state.error = '';
            sync();
        } catch (err) { /* núcleo aún no listo: se reintenta al conectar */ }
    }

    // ── Al oír la palabra ──────────────────────────────────────

    function flashOrb() {
        const orb = $('status-indicator');
        const status = $('status-text');
        const HEARD_TEXT = 'Te escucho...';
        const previous = status && status.textContent !== HEARD_TEXT ? status.textContent : flashOrb.previous;
        flashOrb.previous = previous;
        if (orb) {
            orb.classList.remove('is-heard');
            void orb.offsetWidth;   // reinicia la animación si llegan dos seguidas
            orb.classList.add('is-heard');
        }
        if (status) status.textContent = HEARD_TEXT;
        clearTimeout(flashOrb.timer);
        flashOrb.timer = setTimeout(() => {
            orb?.classList.remove('is-heard');
            // Si ningún evento del agente cambió el estado, vuelve el de antes.
            if (status && status.textContent === HEARD_TEXT && previous) status.textContent = previous;
        }, HEARD_MS);
    }

    async function onWake(data) {
        if (!state.enabled || !state.listening) return;
        stopCapture();               // deja de mandar user:wake_audio y suelta el micrófono para la voz
        flashOrb();
        const opened = await window.gminiVoiceControls?.open?.();
        const command = String(data?.command || '').trim();
        if (command) window.gminiComposer?.send?.(command);
        // Si la voz no se abrió, se vuelve a escuchar; si se abrió, vuelve al terminar.
        if (!opened) sync();
    }

    // ── Indicadores ────────────────────────────────────────────

    function render() {
        const listening = state.enabled && state.listening;
        const paused = state.enabled && !state.listening && !state.starting && !state.error && voiceBusy();
        const button = $('btn-wake');
        if (button) {
            button.hidden = !hasMic;
            button.classList.toggle('is-on', state.enabled);
            button.classList.toggle('is-listening', listening);
            button.classList.toggle('is-starting', state.starting);
            button.classList.toggle('has-error', !!state.error);
            button.setAttribute('aria-pressed', state.enabled ? 'true' : 'false');
            let label = `Manos libres: activar con ${phrase()}`;
            if (state.error) label = `Manos libres: ${state.error}`;
            else if (state.starting) label = 'Manos libres: abriendo el micrófono...';
            else if (listening) label = `Manos libres activo: micrófono abierto, di ${phrase()}. Pulsa para desactivar`;
            else if (paused) label = 'Manos libres en pausa mientras hablas por voz. Pulsa para desactivar';
            button.title = label;
            button.setAttribute('aria-label', label);
        }
        const chip = $('wake-chip');
        if (chip) {
            chip.hidden = !listening;
            chip.title = `Manos libres: di ${phrase()} y lo que necesites`;
        }
        const toggle = $('cb-wake-word');
        if (toggle) toggle.checked = state.enabled;
        const status = $('wake-settings-status');
        if (status) {
            status.classList.toggle('is-error', !!state.error);
            if (state.error) status.textContent = state.error;
            else if (state.starting) status.textContent = 'Abriendo el micrófono...';
            else if (listening) status.textContent = `Escuchando. Di ${phrase()} y, si quieres, lo que necesitas en la misma frase.`;
            else if (paused) status.textContent = 'En pausa mientras hablas por voz; vuelve a escuchar al terminar.';
            else status.textContent = '';
        }
    }

    // ── Arranque ───────────────────────────────────────────────

    $('btn-wake')?.addEventListener('click', () => setEnabled(!state.enabled));
    $('cb-wake-word')?.addEventListener('change', async (e) => {
        const wanted = e.target.checked;
        e.target.disabled = true;
        await setEnabled(wanted);
        e.target.disabled = false;
        render();
    });

    if (typeof voiceRealtime !== 'undefined') voiceRealtime.onChange(() => sync());
    if (typeof ws !== 'undefined') {
        ws.on('agent:wake', onWake);
        ws.on('connected', loadFromConfig);
        ws.on('config:updated', loadFromConfig);
        if (ws.connected) loadFromConfig();
    }
    if (typeof commandPalette !== 'undefined') {
        commandPalette.registerSource(() => (hasMic ? [{
            id: 'voice.wake',
            title: state.enabled ? 'Desactivar manos libres' : `Activar manos libres (${phrase()})`,
            group: 'Agente',
            icon: 'mic-vocal',
            keywords: ['oye', 'palabra de activacion', 'microfono', 'voz', 'wake'],
            checked: state.enabled,
            run: () => setEnabled(!state.enabled),
        }] : []));
    }
    render();

    window.gminiWake = {
        setEnabled,
        sync,
        get enabled() { return state.enabled; },
        get listening() { return state.listening; },
    };
})();
