/**
 * G-Mini Agent — Identidad del agente en la interfaz.
 *
 * Fuente de verdad: config agent.name (nombre), agent.soul (carácter, solo se
 * edita desde la UI) y app.language. El agente puede renombrarse desde el chat
 * (acción agent_rename): tras un config:updated se vuelve a leer agent.name.
 * "G-Mini Agent" sigue siendo el nombre del producto; aquí solo se trata el
 * nombre con el que se presenta el agente (cabecera, saludo, avatar, avisos).
 * El orbe de la cabecera lleva el estado (setStatus en app.js) y la emoción
 * (agent:emotion) como un halo que se apaga solo.
 */
(function () {
    'use strict';

    const DEFAULT_NAME = 'G-Mini';
    const API = 'http://127.0.0.1:8765/api';
    const SOUL_MAX = 600;
    // Mismas reglas que el núcleo: letras, números, espacios y . ' - (hasta 32).
    const NAME_PATTERN = /^[\p{L}\p{N} .'-]{1,32}$/u;
    const EMOTIONS = {
        happy: 'contento',
        sad: 'triste',
        angry: 'molesto',
        surprised: 'sorprendido',
        relaxed: 'tranquilo',
        neutral: 'neutral',
    };
    const STATES = {
        idle: 'listo',
        thinking: 'pensando',
        responding: 'respondiendo',
        paused: 'en pausa',
        error: 'con error',
        disconnected: 'sin conexión',
    };

    let agentName = '';
    let backendName = '';
    let emotionTimer = null;

    const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 32);
    const $ = (id) => document.getElementById(id);

    function cachedName() {
        try { return localStorage.getItem('gmini_agent_name') || ''; } catch (e) { return ''; }
    }

    // El cache local evita ver "G-Mini" un instante antes de que llegue la config,
    // y lo comparte con el avatar (misma origen file://).
    function cacheName(value) {
        try {
            if (value && value !== DEFAULT_NAME) localStorage.setItem('gmini_agent_name', value);
            else localStorage.removeItem('gmini_agent_name');
        } catch (e) { /* sin storage */ }
    }

    function currentName() {
        return agentName || backendName || cachedName() || DEFAULT_NAME;
    }

    function refreshNameSlots() {
        const name = currentName();
        const header = $('agent-name');
        if (header) header.textContent = name;
        document.querySelectorAll('[data-agent-name]').forEach((el) => { el.textContent = name; });
        const preview = $('identity-preview-name');
        if (preview && document.activeElement !== $('input-agent-name')) preview.textContent = name;
        updateOrbLabel();
    }

    function updateOrbLabel() {
        const orb = $('status-indicator');
        if (!orb) return;
        const state = Object.keys(STATES).find((key) => orb.classList.contains(key)) || 'idle';
        const emotion = orb.dataset.emotion && orb.dataset.emotion !== 'neutral'
            ? `, ${EMOTIONS[orb.dataset.emotion] || orb.dataset.emotion}`
            : '';
        const label = `${currentName()}: ${STATES[state]}${emotion}`;
        orb.setAttribute('aria-label', label);
        orb.title = label;
    }

    function updateSoulCount() {
        const soul = $('input-agent-soul');
        const count = $('agent-soul-count');
        if (soul && count) count.textContent = `${soul.value.length} / ${SOUL_MAX}`;
    }

    /** Sección agent de la config (sincronización de Configuración o config:updated). */
    function applyAgentConfig(agentConfig = {}) {
        agentName = clean(agentConfig.name);
        cacheName(agentName);
        const nameInput = $('input-agent-name');
        if (nameInput && document.activeElement !== nameInput) nameInput.value = agentName;
        const soul = $('input-agent-soul');
        if (soul && document.activeElement !== soul && typeof agentConfig.soul === 'string') {
            soul.value = agentConfig.soul.slice(0, SOUL_MAX);
            updateSoulCount();
        }
        refreshNameSlots();
    }

    /** Sección app: idioma del agente. */
    function applyAppConfig(appConfig = {}) {
        const select = $('select-agent-language');
        const lang = String(appConfig.language || '').toLowerCase();
        if (select && ['es', 'en', 'pt'].includes(lang) && document.activeElement !== select) select.value = lang;
    }

    /** Relee agent.name del núcleo (tras config:updated, p. ej. agent_rename). */
    async function refreshFromBackend() {
        try {
            const resp = await fetch(`${API}/config/agent`);
            if (!resp.ok) return;
            const data = await resp.json();
            applyAgentConfig(data?.data?.agent || {});
        } catch (e) { /* núcleo no listo */ }
    }

    /** Nombre publicado por el núcleo, si lo hay (respaldo cuando agent.name está vacío). */
    async function fetchBackendName() {
        try {
            const resp = await fetch(`${API}/health`);
            if (!resp.ok) return;
            const data = await resp.json();
            const name = clean(data?.name || data?.agent_name);
            if (name) {
                backendName = name;
                refreshNameSlots();
            }
        } catch (e) { /* núcleo no listo */ }
    }

    function setEmotion(emotion) {
        const orb = $('status-indicator');
        if (!orb) return;
        const key = Object.prototype.hasOwnProperty.call(EMOTIONS, emotion) ? emotion : 'neutral';
        orb.dataset.emotion = key;
        updateOrbLabel();
        clearTimeout(emotionTimer);
        if (key !== 'neutral') {
            emotionTimer = setTimeout(() => {
                orb.dataset.emotion = 'neutral';
                updateOrbLabel();
            }, 12000);
        }
    }

    async function saveIdentity() {
        const meta = $('agent-identity-meta');
        const nameInput = $('input-agent-name');
        const soulInput = $('input-agent-soul');
        const name = clean(nameInput?.value) || DEFAULT_NAME;
        if (!NAME_PATTERN.test(name)) {
            if (meta) {
                meta.textContent = 'El nombre solo puede tener letras, números, espacios y . \' - (hasta 32).';
                meta.classList.add('is-error');
            }
            nameInput?.focus();
            return false;
        }
        const save = window.settingsManager?._saveConfigValue?.bind(window.settingsManager);
        if (!save) return false;
        const soul = String(soulInput?.value || '').slice(0, SOUL_MAX);
        const okName = await save('agent', 'name', name);
        const okSoul = await save('agent', 'soul', soul);
        if (okName) {
            agentName = name;
            cacheName(name);
            refreshNameSlots();
        }
        if (meta) {
            meta.classList.toggle('is-error', !(okName && okSoul));
            meta.textContent = okName && okSoul
                ? 'Guardado. El agente ya se presenta con este nombre y carácter.'
                : 'No se pudo guardar: revisa el nombre o la conexión con el núcleo.';
        }
        return okName && okSoul;
    }

    function initSettingsFields() {
        const nameInput = $('input-agent-name');
        const preview = $('identity-preview-name');
        nameInput?.addEventListener('input', () => {
            if (preview) preview.textContent = clean(nameInput.value) || DEFAULT_NAME;
        });
        nameInput?.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            saveIdentity();
        });
        $('input-agent-soul')?.addEventListener('input', updateSoulCount);
        $('btn-save-agent-identity')?.addEventListener('click', () => { saveIdentity(); });
        $('select-agent-language')?.addEventListener('change', async (e) => {
            const ok = await window.settingsManager?._saveConfigValue?.('app', 'language', e.target.value);
            if (typeof chatManager !== 'undefined') chatManager._toast(ok ? 'Idioma del agente actualizado.' : 'No se pudo guardar el idioma.', !ok);
        });
        updateSoulCount();
    }

    window.gminiIdentity = {
        DEFAULT_NAME,
        name: currentName,
        applyAgentConfig,
        applyAppConfig,
        refreshFromBackend,
        refreshNameSlots,
        fetchBackendName,
        setEmotion,
        updateOrbLabel,
        saveIdentity,
    };

    initSettingsFields();
    refreshNameSlots();
})();
