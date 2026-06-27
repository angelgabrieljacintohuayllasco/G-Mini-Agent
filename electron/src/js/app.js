/**
 * G-Mini Agent — App Main Controller
 * Punto de entrada principal del frontend. Conecta todo.
 */

(function () {
    'use strict';

    // ── SVG icon constants (replacing emojis) ────────
    // Mic: push-to-talk voice input
    const SVG_MIC = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
    // Waveform: realtime voice conversation (native Live API)
    const SVG_WAVEFORM = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="8" x2="4" y2="16"/><line x1="8" y1="4" x2="8" y2="20"/><line x1="12" y1="6" x2="12" y2="18"/><line x1="16" y1="4" x2="16" y2="20"/><line x1="20" y1="8" x2="20" y2="16"/></svg>';
    // Simulated voice: mic with waves (STT → LLM → TTS pipeline)
    const SVG_MIC_SIMULATED = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><path d="M18 4c2 2 2 6 0 8" opacity="0.5"/><path d="M20 2c3 3 3 10 0 13" opacity="0.3"/></svg>';
    const SVG_STOP = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>';
    const SVG_RECORD = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="8" fill="#ef4444"/></svg>';
    const SVG_MONITOR = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>';

    function _setButtonIcon(btn, svgHtml) {
        if (btn) btn.innerHTML = svgHtml;
    }

    // ── DOM elements ──────────────────────────────────
    const userInput = document.getElementById('user-input');
    const btnSend = document.getElementById('btn-send');
    const btnAttach = document.getElementById('btn-attach');
    const btnAttachFolder = document.getElementById('btn-attach-folder');
    const attachmentChips = document.getElementById('attachment-chips');

    // ── TTS del navegador (Web Speech API) ────────────
    // Voces del SO/Chromium. Cero red, instantáneo. Config en localStorage
    // (la escribe Settings): webspeech_voice, webspeech_lang, webspeech_rate.
    const webSpeech = {
        _supported: typeof window.speechSynthesis !== 'undefined',
        _defaultLang: 'es-ES',
        _pickVoice() {
            if (!this._supported) return null;
            const voices = window.speechSynthesis.getVoices() || [];
            if (!voices.length) return null;
            const wanted = (localStorage.getItem('webspeech_voice') || '').trim();
            if (wanted) {
                const m = voices.find((v) => v.name === wanted);
                if (m) return m;
            }
            const langPref = (localStorage.getItem('webspeech_lang') || this._defaultLang)
                .slice(0, 2).toLowerCase();
            return voices.find((v) => (v.lang || '').toLowerCase().startsWith(langPref))
                || voices.find((v) => (v.lang || '').toLowerCase().startsWith('es'))
                || voices[0];
        },
        // ── Lipsync simulado ──────────────────────────
        // Web Speech no da PCM, así que no hay amplitud real para mover la boca.
        // Simulamos: mientras habla, oscilamos runtime.mouth (jitter + picos por
        // palabra vía onboundary). Eso reactiva la boca 'aa' y los gestos de manos
        // del avatar (vrmSkin usa mouth>0.05 para "talking").
        _active: 0,
        _mouthTimer: null,
        _peak: 0,
        _mouthTick() {
            this._peak = Math.max(0.15, this._peak * 0.55);   // decae el pico
            const m = Math.min(1, this._peak + Math.random() * 0.3);
            try { pushOverlayCharacterRuntime({ status: agentRuntimeState, mouth: m }); } catch (e) { /* noop */ }
        },
        _mouthStart() {
            this._active += 1;
            if (this._mouthTimer) return;
            this._peak = 0.6;
            this._mouthTimer = setInterval(() => this._mouthTick(), 60);
        },
        _mouthStop() {
            this._active = Math.max(0, this._active - 1);
            if (this._active > 0) return;   // aún hay frases en cola
            this._mouthReset();
        },
        _mouthReset() {
            this._active = 0;
            if (this._mouthTimer) { clearInterval(this._mouthTimer); this._mouthTimer = null; }
            try { pushOverlayCharacterRuntime({ status: agentRuntimeState, mouth: 0 }); } catch (e) { /* noop */ }
        },
        speak(text) {
            if (!this._supported) { console.warn('Web Speech no soportado'); return; }
            try {
                const u = new SpeechSynthesisUtterance(text);
                const v = this._pickVoice();
                if (v) { u.voice = v; u.lang = v.lang; }
                else { u.lang = localStorage.getItem('webspeech_lang') || this._defaultLang; }
                const rate = parseFloat(localStorage.getItem('webspeech_rate') || '1');
                u.rate = Math.min(2, Math.max(0.5, Number.isFinite(rate) ? rate : 1));
                u.onstart = () => this._mouthStart();
                u.onend = () => this._mouthStop();
                u.onerror = () => this._mouthStop();
                u.onboundary = () => { this._peak = 1; };  // pico por palabra (si el motor lo emite)
                window.speechSynthesis.speak(u);  // encola nativamente
            } catch (e) { console.error('Web Speech error:', e); }
        },
        cancel() {
            if (this._supported) { try { window.speechSynthesis.cancel(); } catch (e) { /* noop */ } }
            this._mouthReset();
        },
        speaking() { return this._supported && window.speechSynthesis.speaking; },
    };
    if (webSpeech._supported) {
        window.speechSynthesis.getVoices();  // dispara carga async de voces
        window.speechSynthesis.addEventListener('voiceschanged', () => webSpeech._pickVoice());
    }
    window.__webSpeech = webSpeech;  // accesible para voiceRealtime (gate de micrófono)
    const btnAgentStart = document.getElementById('btn-agent-start');
    const btnAgentPause = document.getElementById('btn-agent-pause');
    const btnAgentStop = document.getElementById('btn-agent-stop');
    const btnMinimize = document.getElementById('btn-minimize');
    const btnClose = document.getElementById('btn-close');
    const statusDot = document.getElementById('status-indicator');
    const statusText = document.getElementById('status-text');
    const charCount = document.getElementById('char-count');
    const subagentsBar = document.getElementById('subagents-bar');
    const terminalsBar = document.getElementById('terminals-bar');

    let isGenerating = false;
    let agentRuntimeState = 'idle';

    function pushOverlayCharacterRuntime(payload) {
        if (!window.gmini || typeof window.gmini.overlaySetCharacterRuntime !== 'function') return;
        void window.gmini.overlaySetCharacterRuntime(payload).catch((error) => {
            console.warn('[Overlay] No se pudo actualizar runtime del personaje:', error);
        });
    }

    // Reenvia eventos del agente a la burbuja del mini-chat (skinWindow), si existe.
    function _relaySkinChat(event, data) {
        if (!window.gmini || typeof window.gmini.skinChatRelay !== 'function') return;
        void window.gmini.skinChatRelay({ event, data }).catch(() => {});
    }

    // Notifica a la skinWindow el estado del boton de voz en tiempo real
    // (disponibilidad/activo) para reflejarlo en el boton mic del avatar.
    function _pushSkinVoiceState(payload) {
        if (!window.gmini || typeof window.gmini.skinVoiceState !== 'function') return;
        void window.gmini.skinVoiceState(payload).catch(() => {});
    }

    // Empuja la amplitud del audio de voz en tiempo real a ~20Hz para animar
    // la boca del avatar mientras dura la conversacion realtime.
    let _mouthPushTimer = null;
    function _startMouthPusher() {
        if (_mouthPushTimer) return;
        _mouthPushTimer = setInterval(() => {
            pushOverlayCharacterRuntime({ status: agentRuntimeState, mouth: voiceRealtime.getLevel() });
        }, 50);
    }
    function _stopMouthPusher() {
        if (_mouthPushTimer) {
            clearInterval(_mouthPushTimer);
            _mouthPushTimer = null;
        }
        pushOverlayCharacterRuntime({ status: agentRuntimeState, mouth: 0 });
    }

    // ── Modo avatar: reproduccion de TTS no-streaming con boca animada ──────
    let _skinMode = 'chat';
    if (window.gmini) {
        if (typeof window.gmini.skinGetState === 'function') {
            window.gmini.skinGetState().then((state) => {
                _skinMode = state?.mode === 'skin' ? 'skin' : 'chat';
            }).catch(() => {});
        }
        if (typeof window.gmini.onSkinState === 'function') {
            window.gmini.onSkinState((state) => {
                _skinMode = state?.mode === 'skin' ? 'skin' : 'chat';
                if (_skinMode === 'skin') {
                    _pushSkinVoiceState({ active: voiceRealtime.active, available: !!_realtimeMode });
                }
            });
        }
    }

    let _ttsAudioCtx = null;
    let _ttsAnalyser = null;
    let _ttsAnalyserBuffer = null;
    let _ttsMouthTimer = null;

    function _base64ToArrayBuffer(b64) {
        const raw = atob(b64);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
        return bytes.buffer;
    }

    async function _playSkinTtsAudio(audioB64) {
        try {
            if (!_ttsAudioCtx) {
                _ttsAudioCtx = new AudioContext();
            }
            const audioBuffer = await _ttsAudioCtx.decodeAudioData(_base64ToArrayBuffer(audioB64));
            const source = _ttsAudioCtx.createBufferSource();
            source.buffer = audioBuffer;

            if (!_ttsAnalyser) {
                _ttsAnalyser = _ttsAudioCtx.createAnalyser();
                _ttsAnalyser.fftSize = 256;
                _ttsAnalyser.smoothingTimeConstant = 0.6;
                _ttsAnalyserBuffer = new Uint8Array(_ttsAnalyser.fftSize);
            }
            source.connect(_ttsAnalyser);
            _ttsAnalyser.connect(_ttsAudioCtx.destination);

            if (_ttsMouthTimer) clearInterval(_ttsMouthTimer);
            _ttsMouthTimer = setInterval(() => {
                _ttsAnalyser.getByteTimeDomainData(_ttsAnalyserBuffer);
                let sumSquares = 0;
                for (let i = 0; i < _ttsAnalyserBuffer.length; i++) {
                    const norm = (_ttsAnalyserBuffer[i] - 128) / 128;
                    sumSquares += norm * norm;
                }
                const rms = Math.sqrt(sumSquares / _ttsAnalyserBuffer.length);
                pushOverlayCharacterRuntime({ status: agentRuntimeState, mouth: Math.min(1, rms * 4) });
            }, 50);

            source.onended = () => {
                clearInterval(_ttsMouthTimer);
                _ttsMouthTimer = null;
                pushOverlayCharacterRuntime({ status: agentRuntimeState, mouth: 0 });
            };
            source.start();
        } catch (err) {
            console.warn('[Skin] No se pudo reproducir audio TTS:', err);
        }
    }

    // ── Initialize modules ────────────────────────────
    chatManager.init();
    settingsManager.init();
    historyManager.init();
    if (window.codeManager?.init) {
        window.codeManager.init();
    }

    // ── WebSocket events ──────────────────────────────

    ws.on('connected', () => {
        agentRuntimeState = 'idle';
        setStatus('idle', 'Conectado');
        updateAgentControls();
        settingsManager.updateModelLabel();
        pushOverlayCharacterRuntime({ status: 'idle', visemes: [], audioHintMs: 0 });

        // Si el catálogo no cargó aún (backend tardó en arrancar), recargarlo ahora.
        // Esto ocurre cuando el backend tarda más de 1.5s en estar listo al iniciar.
        if (Object.keys(MODEL_OPTIONS).length === 0 && settingsManager) {
            settingsManager._loadModelsCatalog().then(async () => {
                await settingsManager._syncFromBackend();
            });
        } else {
            // Catálogo ya disponible: solo verificar soporte RT con provider+modelo actuales
            ws.checkRealtimeAvailable(
                settingsManager?.currentProvider || '',
                settingsManager?.currentModel || ''
            );
        }
    });

    // Sesión anterior restaurada por el backend al reconectar
    ws.on('session:restored', (data) => {
        if (!data || !data.session_id) return;
        const messages = data.messages || [];

        // Actualizar sidebar con la sesión activa
        historyManager.currentSessionId = data.session_id;
        historyManager.loadSessions();

        // Renderizar mensajes históricos en el chat
        chatManager.clear();
        if (messages.length > 0) {
            messages.forEach(msg => {
                const meta = msg.metadata || {};
                const msgType = msg.message_type || 'text';

                // Tool calls se muestran como action cards
                if (meta.tool_name) {
                    const cardEl = chatManager.addActionCard(
                        meta.tool_name,
                        meta.params || {}
                    );
                    chatManager.updateActionCard(
                        cardEl,
                        meta.success !== false,
                        meta.result_preview || ''
                    );
                } else if (msg.role === 'display' || msgType === 'system' || msgType === 'action' || msgType === 'error' || msgType === 'warning') {
                    // Agent activity messages (system, action results, errors)
                    const cssClass = msgType === 'error' ? 'error-message'
                        : msgType === 'warning' ? 'warning-message'
                        : msgType === 'action' ? 'system-message action-result'
                        : 'system-message';
                    const el = chatManager._createMessageEl(cssClass);
                    el.innerHTML = chatManager._renderMarkdown(msg.content);
                    chatManager.messagesContainer.appendChild(el);
                } else if (msg.role === 'user') {
                    chatManager.addUserMessage(msg.content);
                } else if (msg.role === 'assistant') {
                    const el = chatManager._createMessageEl('assistant-message');
                    el.innerHTML = chatManager._renderMarkdown(msg.content);
                    chatManager.messagesContainer.appendChild(el);
                }
            });
            chatManager._scrollToBottom();
        }
    });

    ws.on('disconnected', () => {
        agentRuntimeState = 'disconnected';
        setStatus('disconnected', 'Desconectado');
        setGenerating(false);
        pendingActionCards.clear();
        updateAgentControls();
        pushOverlayCharacterRuntime({ status: 'idle', visemes: [], audioHintMs: 0 });
    });

    ws.on('error', (data) => {
        agentRuntimeState = 'error';
        setStatus('error', `Error: ${data?.message || 'Desconocido'}`);
        updateAgentControls();
        pushOverlayCharacterRuntime({ status: 'idle', visemes: [], audioHintMs: 0 });
    });

    ws.on('agent:message', (data) => {
        chatManager.handleAgentMessage(data);
        _relaySkinChat('agent:message', data);

        // Update overlay
        if (data.text && window.gmini) {
            window.gmini.setOverlayText(data.text);
        }

        // Refresh history when message stream ends
        if (data.done) {
            historyManager.refreshCurrentSession();
        }
    });

    ws.on('agent:approval', (data) => {
        chatManager.renderApprovalState(data);
        if (data?.pending) {
            agentRuntimeState = 'thinking';
            setStatus('thinking', 'Esperando aprobacion...');
            setGenerating(false);
        }
    });

    ws.on('agent:subagents', (data) => {
        renderSubagents(data);
    });

    ws.on('gateway:notification', (data) => {
        const title = String(data?.title || 'Notificacion');
        const body = String(data?.body || '');
        const source = String(data?.source_type || '').trim();
        const sourceLabel = source ? ` [${source}]` : '';
        const text = `Gateway${sourceLabel}: ${title}${body ? `\n${body}` : ''}`;
        chatManager.addSystemMessage(text);
        if (window.codeManager?.refreshGateway) {
            window.codeManager.refreshGateway();
        }
    });

    ws.on('agent:status', (data) => {
        const status = data?.status || 'idle';
        agentRuntimeState = status;
        _relaySkinChat('agent:status', data);
        pushOverlayCharacterRuntime(
            status === 'responding' || status === 'calling'
                ? { status }
                : { status, visemes: [], audioHintMs: 0 }
        );
        switch (status) {
            case 'thinking':
                setStatus('thinking', 'Pensando...');
                setGenerating(true);
                break;
            case 'responding':
                setStatus('responding', 'Respondiendo...');
                setGenerating(true);
                break;
            case 'executing':
                setStatus('responding', 'Ejecutando acción...');
                setGenerating(true);
                break;
            case 'paused':
                setStatus('paused', 'Pausado');
                setGenerating(true);
                break;
            case 'realtime_connecting':
                setStatus('responding', 'Conectando voz…');
                setGenerating(true);
                break;
            case 'realtime_active':
                setStatus('responding', 'Voz en tiempo real');
                break;
            case 'realtime_stopped':
                setStatus('idle', 'Listo');
                setGenerating(false);
                break;
            case 'idle':
            default:
                setStatus('idle', 'Listo');
                setGenerating(false);
                break;
        }
    });

    // Pitido corto "ya te escucho" (dos tonos ascendentes). WebAudio puro, sin assets.
    let _cueCtx = null;
    function _playListeningCue() {
        try {
            _cueCtx = _cueCtx || new (window.AudioContext || window.webkitAudioContext)();
            const ctx = _cueCtx;
            if (ctx.state === 'suspended') ctx.resume();
            const t = ctx.currentTime;
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(880, t);          // A5
            osc.frequency.setValueAtTime(1175, t + 0.09);  // D6 — "di-dít"
            gain.gain.setValueAtTime(0.0001, t);
            gain.gain.exponentialRampToValueAtTime(0.18, t + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
            osc.connect(gain).connect(ctx.destination);
            osc.start(t);
            osc.stop(t + 0.24);
        } catch (e) {
            // Audio bloqueado por el navegador: el cue es opcional, no romper.
        }
    }

    ws.on('agent:audio_interrupt', () => {
        // Barge-in: el usuario interrumpió. Cortar el audio encolado de la IA.
        if (typeof voiceRealtime !== 'undefined' && voiceRealtime.active) {
            voiceRealtime.clearPlayback();
        }
        webSpeech.cancel();
    });

    // La sesión RT tarda ~3s en conectar (handshake Google Live). Cuando el backend
    // confirma que ya está escuchando, dar un pitido corto para que el usuario sepa
    // que puede hablar (antes hablaba al vacío durante esos segundos).
    ws.on('agent:realtime_ready', () => {
        if (typeof voiceRealtime !== 'undefined' && voiceRealtime.active) {
            _playListeningCue();
        }
    });

    ws.on('agent:audio', (data) => {
        if (!data) return;
        // Realtime streaming audio — reproducir directamente
        if (data.stream && data.audio && typeof voiceRealtime !== 'undefined' && voiceRealtime.active) {
            const durationMs = voiceRealtime.playAudioChunk(data.audio, data.format || 'pcm16');
            if (durationMs > 0) {
                pushOverlayCharacterRuntime({
                    status: agentRuntimeState,
                    audioHintMs: Math.round(durationMs),
                });
            }
            return;
        }
        // Non-realtime TTS — en modo avatar reproducir con boca animada
        if (_skinMode === 'skin' && data.audio) {
            void _playSkinTtsAudio(data.audio);
        }
        const durationMs = Number.isFinite(Number(data?.duration))
            ? Math.max(0, Math.round(Number(data.duration) * 1000))
            : 1800;
        pushOverlayCharacterRuntime({
            status: agentRuntimeState,
            audioHintMs: durationMs || 1800,
        });
    });

    // TTS del navegador (Web Speech): el backend manda el texto y aquí lo hablamos
    // con speechSynthesis (voces del SO, cero latencia de red). Se encola nativamente.
    ws.on('agent:speak', (data) => {
        const text = String(data?.text || '').trim();
        if (text) webSpeech.speak(text);
    });

    ws.on('agent:lipsync', (data) => {
        pushOverlayCharacterRuntime({
            status: agentRuntimeState,
            visemes: Array.isArray(data?.visemes) ? data.visemes : [],
        });
    });

    ws.on('agent:emotion', (data) => {
        if (!data || typeof data.emotion !== 'string') return;
        pushOverlayCharacterRuntime({
            status: agentRuntimeState,
            emotion: data.emotion,
        });
    });

    ws.on('agent:screenshot', (data) => {
        if (data && data.image) {
            console.log(`[Screenshot] Received: ${data.image.length} chars, starts: ${data.image.substring(0, 30)}`);
            chatManager.addScreenshot(data.image, data.caption || '');
        } else {
            console.warn('[Screenshot] Event received but no image data:', data);
        }
    });

    ws.on('agent:media', (data) => {
        if (data && data.url && data.type) {
            const fullUrl = `http://127.0.0.1:8765${data.url}`;
            chatManager.addMediaPlayer(data.type, fullUrl, data.filename || '');
        }
    });

    // ── Action visualization events ───────────────────
    // Map para asociar action cards con su ID (para actualizar con resultado)
    const pendingActionCards = new Map();

    ws.on('agent:action', (data) => {
        if (!data) return;
        const { type, params, actionId } = data;

        // Mostrar tarjeta de actividad en el chat
        const cardEl = chatManager.addActionCard(type, params || {});
        if (actionId) {
            pendingActionCards.set(actionId, cardEl);
        }

        // Mostrar burbuja del cursor para acciones con coordenadas
        if (params && params.x !== undefined && params.y !== undefined) {
            if (window.gmini && window.gmini.showCursorBubble) {
                window.gmini.showCursorBubble(params.x, params.y);
            }
        }

        // Mostrar efectos visuales según el tipo de acción
        switch (type) {
            case 'click':
            case 'double_click':
            case 'right_click':
                if (params.x !== undefined && params.y !== undefined) {
                    overlayEffects.showClickAt(params.x, params.y, type);
                }
                break;
            case 'screenshot':
                overlayEffects.showScreenshotEffect();
                if (window.gmini && window.gmini.showScreenshotOverlay) {
                    window.gmini.showScreenshotOverlay();
                }
                break;
            case 'move':
                if (params.x !== undefined && params.y !== undefined) {
                    overlayEffects.showMove(params.x, params.y);
                }
                break;
            case 'type':
                overlayEffects.showTypingEffect(params.text || '');
                break;
            case 'press':
                overlayEffects.showKeyPress(params.key || '');
                break;
            case 'hotkey':
                overlayEffects.showHotkey(params.keys || '');
                break;
            case 'scroll':
                overlayEffects.showScroll(params.clicks || 0);
                break;
            case 'drag':
                if (params.x !== undefined && params.y !== undefined) {
                    overlayEffects.showDrag(params.x, params.y);
                }
                break;
            case 'wait':
                overlayEffects.showWait(params.seconds || 1);
                break;
        }
    });

    // ── Action result events (actualiza la card con éxito/error) ───
    ws.on('agent:action_result', (data) => {
        if (!data || !data.actionId) return;
        const cardEl = pendingActionCards.get(data.actionId);
        if (cardEl) {
            chatManager.updateActionCard(cardEl, data.success, data.result || '', data.durationMs);
            pendingActionCards.delete(data.actionId);
        }
    });

    // Limpiar action cards pendientes cuando el agente vuelve a idle
    ws.on('agent:status', (data) => {
        if ((data?.status || 'idle') === 'idle') {
            pendingActionCards.clear();
        }
    });

    ws.on('agent:executing', (data) => {
        const active = data?.active || false;
        overlayEffects.setExecutingMode(active);
        
        // Cambiar opacidad de la ventana
        if (window.gmini && window.gmini.setExecutingMode) {
            window.gmini.setExecutingMode(active);
        }
    });

    // ── Connect to backend ────────────────────────────
    ws.connect();
    setInterval(refreshTerminals, 5000);
    setTimeout(refreshTerminals, 1500);

    // ── Input handling ────────────────────────────────

    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    userInput.addEventListener('input', () => {
        // Auto-resize
        userInput.style.height = 'auto';
        userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
        // Char count
        charCount.textContent = userInput.value.length;
    });

    btnSend.addEventListener('click', sendMessage);

    // ── Adjuntos (clip) ───────────────────────────────
    let pendingAttachments = [];  // [{ kind, file_name, local_path }]

    function renderAttachmentChips() {
        if (!attachmentChips) return;
        if (!pendingAttachments.length) {
            attachmentChips.style.display = 'none';
            attachmentChips.innerHTML = '';
            return;
        }
        attachmentChips.style.display = 'flex';
        attachmentChips.innerHTML = '';
        pendingAttachments.forEach((att, i) => {
            const chip = document.createElement('span');
            chip.className = 'attachment-chip';
            const label = document.createElement('span');
            label.className = 'attachment-chip-name';
            label.textContent = att.file_name;
            label.title = att.local_path;
            const rm = document.createElement('button');
            rm.className = 'attachment-chip-remove';
            rm.textContent = '×';
            rm.title = 'Quitar';
            rm.addEventListener('click', () => {
                pendingAttachments.splice(i, 1);
                renderAttachmentChips();
            });
            chip.appendChild(label);
            chip.appendChild(rm);
            attachmentChips.appendChild(chip);
        });
    }

    async function pickAttachments(mode) {
        if (!window.gmini || typeof window.gmini.pickAttachments !== 'function') {
            chatManager.addSystemMessage('Adjuntar archivos no esta disponible en este entorno.');
            return;
        }
        const paths = await window.gmini.pickAttachments(mode) || [];
        for (const p of paths) {
            if (pendingAttachments.some((a) => a.local_path === p)) continue;
            const file_name = String(p).split(/[\\/]/).pop() || p;
            pendingAttachments.push({ kind: mode === 'folder' ? 'folder' : 'file', file_name, local_path: p });
        }
        renderAttachmentChips();
        userInput.focus();
    }

    btnAttach?.addEventListener('click', () => pickAttachments('files'));
    btnAttachFolder?.addEventListener('click', () => pickAttachments('folder'));

    btnAgentStart?.addEventListener('click', () => {
        ws.sendCommand('start');
        if (agentRuntimeState === 'paused') {
            agentRuntimeState = isGenerating ? 'responding' : 'idle';
            updateAgentControls();
        }
    });

    btnAgentPause?.addEventListener('click', () => {
        ws.sendCommand('pause');
        if (isGenerating && agentRuntimeState !== 'paused') {
            agentRuntimeState = 'paused';
            updateAgentControls();
        }
    });

    btnAgentStop?.addEventListener('click', () => {
        ws.sendCommand('stop');
        agentRuntimeState = 'idle';
        setGenerating(false);
    });

    // ── Window controls ───────────────────────────────

    btnMinimize.addEventListener('click', () => {
        if (window.gmini) window.gmini.minimize();
    });

    btnClose.addEventListener('click', () => {
        if (window.gmini) window.gmini.close();
    });

    // ── Helpers ───────────────────────────────────────

    function sendMessage() {
        const text = userInput.value.trim();
        if ((!text && !pendingAttachments.length) || isGenerating) return;

        if (!ws.connected) {
            chatManager.addSystemMessage('No hay conexion con el backend. Intenta de nuevo.');
            return;
        }

        const attachments = pendingAttachments.slice();
        const displayText = attachments.length
            ? `${text}${text ? '\n' : ''}📎 ${attachments.map((a) => a.file_name).join(', ')}`
            : text;
        chatManager.addUserMessage(displayText);
        ws.sendMessage(text, attachments);

        pendingAttachments = [];
        renderAttachmentChips();
        updateComposerValue('');
        userInput.focus();
    }

    // Mensaje enviado desde la burbuja de mini-chat del avatar (skinWindow).
    if (window.gmini && typeof window.gmini.onSkinChatSend === 'function') {
        window.gmini.onSkinChatSend((text) => {
            const trimmed = String(text || '').trim();
            if (!trimmed || isGenerating) return;
            if (!ws.connected) {
                _relaySkinChat('agent:message', {
                    text: 'No hay conexion con el backend. Intenta de nuevo.',
                    type: 'system',
                    done: true,
                });
                return;
            }
            chatManager.addUserMessage(trimmed);
            ws.sendMessage(trimmed);
        });
    }

    function updateComposerValue(value) {
        userInput.value = String(value || '');
        userInput.style.height = 'auto';
        userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
        charCount.textContent = userInput.value.length;
    }

    function setStatus(state, text) {
        statusDot.className = `status-dot ${state}`;
        statusText.textContent = text;
    }

    function setGenerating(value) {
        isGenerating = value;
        btnSend.disabled = value;
        userInput.disabled = value;
        updateAgentControls();
    }

    function updateAgentControls() {
        const paused = agentRuntimeState === 'paused';
        const connected = agentRuntimeState !== 'disconnected' && agentRuntimeState !== 'error';
        const busy = isGenerating || paused;
        const canResume = connected && paused;
        const canPause = connected && isGenerating && !paused;
        const canStop = connected && busy;

        if (btnAgentStart) {
            btnAgentStart.disabled = !canResume;
            btnAgentStart.classList.toggle('active', canResume);
        }
        if (btnAgentPause) {
            btnAgentPause.disabled = !canPause;
            btnAgentPause.classList.toggle('active', canPause);
        }
        if (btnAgentStop) {
            btnAgentStop.disabled = !canStop;
            btnAgentStop.classList.toggle('active', canStop);
        }
    }

    function renderSubagents(data) {
        const items = Array.isArray(data?.items) ? data.items : [];
        const active = items.filter((item) => item.status === 'queued' || item.status === 'running');
        if (active.length === 0) {
            subagentsBar.classList.add('hidden');
            subagentsBar.innerHTML = '';
            return;
        }

        subagentsBar.classList.remove('hidden');
        subagentsBar.innerHTML = active.map((item) => `
            <div class="subagent-pill subagent-${item.status}">
                <span class="subagent-name">${escapeHtml(item.name || item.id)}</span>
                <span class="subagent-status">${escapeHtml(item.status)}</span>
            </div>
        `).join('');
    }

    async function refreshTerminals() {
        try {
            const resp = await fetch('http://127.0.0.1:8765/api/terminals');
            if (!resp.ok) return;
            const data = await resp.json();
            renderTerminals(data);
        } catch (err) {
            // backend not ready
        }
    }

    function renderTerminals(data) {
        const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
        const visible = sessions.slice(0, 5);
        if (visible.length === 0) {
            terminalsBar.classList.add('hidden');
            terminalsBar.innerHTML = '';
            return;
        }

        terminalsBar.classList.remove('hidden');
        terminalsBar.innerHTML = visible.map((item) => {
            const name = escapeHtml(item.shell_name || item.shell_key);
            const status = escapeHtml(item.status || '');
            const lastCmd = item.last_command ? escapeHtml(item.last_command.slice(0, 40)) : '';
            const dur = typeof item.duration_s === 'number' ? `${item.duration_s.toFixed(1)}s` : '';
            const meta = [lastCmd, dur].filter(Boolean).join(' · ');
            return `
                <div class="terminal-pill" title="${escapeHtml(item.last_command || '')}">
                    <span class="subagent-name">${name}</span>
                    <span class="subagent-status">${status}</span>
                    ${meta ? `<span class="terminal-meta">${meta}</span>` : ''}
                </div>
            `;
        }).join('');
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text || '');
        return div.innerHTML;
    }

    // ── Voice capture (btn-voice) ───────────────────────
    const btnVoice = document.getElementById('btn-voice');
    let _voiceRecording = false;
    let _mediaRecorder = null;

    ws.on('agent:stt_result', (data) => {
        const text = (data?.text || '').trim();
        if (text) {
            updateComposerValue(text);
            userInput.focus();
        }
    });

    if (btnVoice) {
        btnVoice.addEventListener('click', async () => {
            if (_voiceRecording) {
                // Stop recording
                if (_mediaRecorder && _mediaRecorder.state !== 'inactive') {
                    _mediaRecorder.stop();
                }
                return;
            }
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                _mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
                const chunks = [];

                _mediaRecorder.ondataavailable = (e) => {
                    if (e.data.size > 0) chunks.push(e.data);
                };
                _mediaRecorder.onstop = async () => {
                    _voiceRecording = false;
                    btnVoice.classList.remove('recording');
                    _setButtonIcon(btnVoice, SVG_MIC);
                    stream.getTracks().forEach((t) => t.stop());

                    if (chunks.length === 0) return;
                    const blob = new Blob(chunks, { type: 'audio/webm' });
                    const buf = await blob.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    let binary = '';
                    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
                    const b64 = btoa(binary);
                    ws.sendAudio(b64);
                };

                _mediaRecorder.start();
                _voiceRecording = true;
                btnVoice.classList.add('recording');
                _setButtonIcon(btnVoice, SVG_STOP);
            } catch (err) {
                console.error('Mic access error:', err);
                _voiceRecording = false;
                if (btnVoice) {
                    btnVoice.classList.remove('recording');
                    _setButtonIcon(btnVoice, SVG_MIC);
                }
            }
        });
    }

    // ── Realtime voice (btn-realtime) ───────────────────
    const btnRealtime = document.getElementById('btn-realtime');
    let _realtimeProvider = ''; // provider RT resuelto por el backend
    let _realtimeVoice = ''; // voz seleccionada por el usuario
    let _realtimeVoices = []; // voces disponibles
    let _realtimeMode = '';  // 'native' | 'simulated'

    // Arranca/detiene la conversacion en tiempo real. Reutilizable desde el
    // boton de la ventana principal y desde el boton mic del avatar (skinWindow).
    async function _toggleRealtimeVoice() {
        if (voiceRealtime.active) {
            await voiceRealtime.stop();
            _stopMouthPusher();
            if (btnRealtime) {
                btnRealtime.classList.remove('realtime-active', 'realtime-simulated');
                _setButtonIcon(btnRealtime, _realtimeMode === 'simulated' ? SVG_MIC_SIMULATED : SVG_WAVEFORM);
                btnRealtime.title = _realtimeMode === 'simulated'
                    ? 'Conversación por voz (STT → Modelo → TTS)'
                    : 'Conversación en tiempo real';
            }
            _pushSkinVoiceState({ active: false, available: !!_realtimeMode });
        } else {
            // Enviar mode al backend para que sepa qué pipeline usar
            const provider = _realtimeProvider || settingsManager?.currentProvider || '';
            const started = await voiceRealtime.start(provider, _realtimeVoice, _realtimeMode);
            if (started) {
                if (btnRealtime) {
                    btnRealtime.classList.add('realtime-active');
                    if (_realtimeMode === 'simulated') {
                        btnRealtime.classList.add('realtime-simulated');
                    }
                    _setButtonIcon(btnRealtime, SVG_RECORD);
                }
                _startMouthPusher();
                _pushSkinVoiceState({ active: true, available: !!_realtimeMode });
            }
        }
    }

    if (btnRealtime) {
        // Ocultar por defecto hasta que el backend confirme soporte RT
        btnRealtime.style.display = 'none';
        btnRealtime.addEventListener('click', () => { void _toggleRealtimeVoice(); });
    }

    // Toggle de voz solicitado desde el boton mic del avatar (skinWindow).
    if (window.gmini && typeof window.gmini.onSkinVoiceToggle === 'function') {
        window.gmini.onSkinVoiceToggle(() => { void _toggleRealtimeVoice(); });
    }

    // Selector de voz para realtime (voice-select existente o creado dinámicamente)
    let _voiceSelect = document.getElementById('realtime-voice-select');

    function _updateVoiceSelector(voices) {
        _realtimeVoices = voices || [];
        if (!_voiceSelect && _realtimeVoices.length > 0) {
            // Crear selector de voz junto al botón realtime
            _voiceSelect = document.createElement('select');
            _voiceSelect.id = 'realtime-voice-select';
            _voiceSelect.className = 'realtime-voice-select';
            _voiceSelect.title = 'Voz del asistente';
            if (btnRealtime && btnRealtime.parentElement) {
                btnRealtime.parentElement.insertBefore(_voiceSelect, btnRealtime);
            }
        }
        if (_voiceSelect) {
            _voiceSelect.innerHTML = _realtimeVoices.map((v) =>
                `<option value="${escapeHtml(v)}" ${v === _realtimeVoice ? 'selected' : ''}>${escapeHtml(v)}</option>`
            ).join('');
            _voiceSelect.style.display = _realtimeVoices.length > 0 ? '' : 'none';
            _voiceSelect.onchange = () => { _realtimeVoice = _voiceSelect.value; };
            if (!_realtimeVoice && _realtimeVoices.length > 0) {
                _realtimeVoice = _realtimeVoices[0];
            }
        }
    }

    // Escuchar respuesta del backend sobre disponibilidad RT
    ws.on('agent:realtime_available', (data) => {
        if (!btnRealtime) return;
        if (data?.available) {
            _realtimeProvider = data.provider || '';
            _realtimeMode = data.mode || 'native';
            btnRealtime.style.display = '';

            // Actualizar apariencia según modo
            if (_realtimeMode === 'simulated') {
                _setButtonIcon(btnRealtime, SVG_MIC_SIMULATED);
                btnRealtime.title = 'Conversación por voz (STT → Modelo → TTS)';
            } else {
                _setButtonIcon(btnRealtime, SVG_WAVEFORM);
                btnRealtime.title = 'Conversación en tiempo real';
            }

            _updateVoiceSelector(data.voices || []);

            // Guardar capacidades del modelo para el botón de video
            _modelSupportsVideo = !!data.supports_video;
            _pushSkinVoiceState({ active: voiceRealtime.active, available: true });
        } else {
            _realtimeProvider = '';
            _realtimeMode = '';
            btnRealtime.style.display = 'none';
            _updateVoiceSelector([]);
            _modelSupportsVideo = false;
            // Ocultar botón de video si el modelo no es compatible
            if (btnVideoStream) {
                btnVideoStream.style.display = 'none';
            }
            // Si estaba activo, detener
            if (voiceRealtime.active) {
                voiceRealtime.stop();
                _stopMouthPusher();
                btnRealtime.classList.remove('realtime-active', 'realtime-simulated');
                _setButtonIcon(btnRealtime, SVG_WAVEFORM);
            }
            _pushSkinVoiceState({ active: false, available: false });
        }
    });

    // Mostrar transcripción del habla del usuario en el chat
    ws.on('agent:realtime_user_text', (data) => {
        const text = (data?.text || '').trim();
        if (text) {
            // Cerrar burbuja de streaming del agente antes de mostrar mensaje del usuario
            if (chatManager.isStreaming) {
                chatManager.finishStreaming();
            }
            chatManager.addUserMessage(text);
        }
    });

    // Actualizar botón RT según estado
    ws.on('agent:status', (data) => {
        const status = data?.status || '';
        if (status === 'realtime_active') {
            if (btnRealtime) {
                btnRealtime.classList.add('realtime-active');
                if (data?.mode === 'simulated') {
                    btnRealtime.classList.add('realtime-simulated');
                }
                _setButtonIcon(btnRealtime, SVG_RECORD);
            }
            // Mostrar botón de video si el modelo tiene live_api: true (siempre soporta video)
            if (btnVideoStream && _modelSupportsVideo) {
                btnVideoStream.style.display = '';
            }
        } else if (status === 'realtime_stopped') {
            if (btnRealtime) {
                btnRealtime.classList.remove('realtime-active', 'realtime-simulated');
                _setButtonIcon(btnRealtime, _realtimeMode === 'simulated' ? SVG_MIC_SIMULATED : SVG_WAVEFORM);
                btnRealtime.title = _realtimeMode === 'simulated'
                    ? 'Conversación por voz (STT → Modelo → TTS)'
                    : 'Conversación en tiempo real';
            }
            // Ocultar y resetear botón de video stream
            if (btnVideoStream) {
                btnVideoStream.style.display = 'none';
                btnVideoStream.classList.remove('video-stream-active');
                _setButtonIcon(btnVideoStream, SVG_MONITOR);
                _videoStreamActive = false;
            }
        }
    });

    // ── Video stream toggle (btn-video-stream) ────────────────
    // Visible para cualquier modelo con live_api: true (siempre tienen video + Google Search)
    // Captura la pantalla del PC a ~1fps y la envía al modelo vía Live API realtimeInput.video
    const btnVideoStream = document.getElementById('btn-video-stream');
    let _videoStreamActive = false;
    let _modelSupportsVideo = false;

    if (btnVideoStream) {
        btnVideoStream.addEventListener('click', () => {
            if (!voiceRealtime.active) return;
            _videoStreamActive = !_videoStreamActive;
            ws.toggleScreenStream(_videoStreamActive);
            btnVideoStream.classList.toggle('video-stream-active', _videoStreamActive);
            _setButtonIcon(btnVideoStream, _videoStreamActive ? SVG_RECORD : SVG_MONITOR);
            btnVideoStream.title = _videoStreamActive
                ? 'Detener streaming de pantalla'
                : 'Mostrar pantalla a la IA (Live API streaming)';
        });
    }

    // Respuesta del backend sobre estado del video stream
    ws.on('agent:screen_stream_status', (data) => {
        _videoStreamActive = !!data?.active;
        if (btnVideoStream) {
            btnVideoStream.classList.toggle('video-stream-active', _videoStreamActive);
            _setButtonIcon(btnVideoStream, _videoStreamActive ? SVG_RECORD : SVG_MONITOR);
            btnVideoStream.title = _videoStreamActive
                ? 'Detener streaming de pantalla'
                : 'Mostrar pantalla a la IA (Live API streaming)';
        }
    });

    window.gminiComposer = {
        setText(text) {
            updateComposerValue(text);
            userInput.focus();
        },
        appendText(text) {
            const addition = String(text || '');
            const nextText = userInput.value
                ? `${userInput.value.trimEnd()}\n\n${addition}`
                : addition;
            updateComposerValue(nextText);
            userInput.focus();
        },
        getText() {
            return userInput.value;
        },
        focus() {
            userInput.focus();
        },
    };
})();
