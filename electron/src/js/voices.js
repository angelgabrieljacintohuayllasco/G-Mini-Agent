/**
 * G-Mini Agent — Configuración > Voz: catálogo de voces con muestra.
 *
 * Para los motores con catálogo (Edge, OpenAI y Gemini) lista las voces de
 * GET /api/voice/voices, permite filtrarlas y escucharlas con "Probar"
 * (POST /api/voice/preview) y guarda la elegida al instante en la clave de
 * ese motor (voice.edge_voice, voice.openai_voice o voice.google_voice).
 * Settings llama a setEngine() cada vez que cambia el motor seleccionado.
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const CONFIG_KEYS = { edge: 'edge_voice', openai: 'openai_voice', google: 'google_voice' };
    const GENDERS = { female: 'femenina', male: 'masculina', neutral: 'neutra' };
    const STYLES = {
        friendly: 'amable', positive: 'positiva', warm: 'cálida', cheerful: 'alegre', calm: 'serena',
        confident: 'segura', professional: 'profesional', authentic: 'natural', expressive: 'expresiva',
        caring: 'cercana', pleasant: 'agradable', lively: 'animada', clear: 'clara', considerate: 'atenta',
        comfort: 'reconfortante', sincere: 'sincera', approachable: 'cercana', rational: 'serena',
        humorous: 'divertida', passion: 'apasionada', reliable: 'confiable', gentle: 'suave',
    };

    const state = { engine: '', provider: '', locale: 'es', voices: [], current: '', query: '', token: 0, loading: false };
    const previews = new Map();
    let playingButton = null;
    const regionNames = (() => {
        try { return new Intl.DisplayNames(['es'], { type: 'region' }); } catch (e) { return null; }
    })();
    const $ = (id) => document.getElementById(id);
    const icon = (name) => window.gminiDom.icon(name);
    const toast = (message, isError = false) => {
        if (typeof chatManager !== 'undefined') chatManager._toast(message, isError);
    };

    async function api(path, options = {}) {
        const resp = await fetch(`${API}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!resp.ok || data?.ok === false) throw new Error(data?.detail || data?.error || `HTTP ${resp.status}`);
        return data || {};
    }

    // ── Presentación de cada voz ───────────────────────────────

    function shortName(voice) {
        const id = String(voice.id || '');
        if (state.provider === 'edge') {
            const tail = id.split('-').slice(2).join('-').replace(/(Multilingual)?Neural$/, '');
            return tail.replace(/([a-z])([A-Z])/g, '$1 $2') || id;
        }
        return id ? id.charAt(0).toUpperCase() + id.slice(1) : '';
    }

    function details(voice) {
        const parts = [];
        if (voice.locale) {
            const region = String(voice.locale).split('-')[1];
            const regionLabel = region && regionNames ? regionNames.of(region) : '';
            parts.push(regionLabel ? `${regionLabel} (${voice.locale})` : voice.locale);
        }
        if (voice.description) parts.push(String(voice.description).toLowerCase());
        if (voice.gender && GENDERS[voice.gender]) parts.push(GENDERS[voice.gender]);
        const styles = (Array.isArray(voice.styles) ? voice.styles : [])
            .map((s) => STYLES[String(s).toLowerCase()] || String(s).toLowerCase())
            .filter((s, i, all) => all.indexOf(s) === i)
            .slice(0, 2);
        if (styles.length) parts.push(styles.join(', '));
        return parts.join(' · ');
    }

    function matches(voice) {
        if (!state.query) return true;
        const hay = `${voice.id} ${shortName(voice)} ${details(voice)}`
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
        return state.query.split(/\s+/).every((word) => hay.includes(word));
    }

    // ── Render ─────────────────────────────────────────────────

    function setMeta(text) {
        const meta = $('voice-catalog-meta');
        if (meta) meta.textContent = text;
    }

    function render() {
        const list = $('voice-catalog');
        if (!list) return;
        list.innerHTML = '';
        // La voz elegida primero; el resto como viene (por idioma y nombre).
        const voices = state.voices.filter(matches)
            .sort((a, b) => (b.id === state.current) - (a.id === state.current));
        voices.forEach((voice) => list.appendChild(renderRow(voice)));
        if (!voices.length) {
            const empty = document.createElement('div');
            empty.className = 'voice-empty';
            empty.textContent = state.voices.length ? 'Ninguna voz coincide con el filtro.' : 'Este motor no tiene voces para elegir.';
            list.appendChild(empty);
        }
        const radios = list.querySelectorAll('.voice-pick');
        const checked = list.querySelector('.voice-pick[aria-checked="true"]');
        radios.forEach((radio) => { radio.tabIndex = -1; });
        (checked || radios[0])?.setAttribute('tabindex', '0');
    }

    function renderRow(voice) {
        const row = document.createElement('div');
        row.className = 'voice-row';
        const selected = voice.id === state.current;
        row.classList.toggle('is-selected', selected);

        const pick = document.createElement('button');
        pick.type = 'button';
        pick.className = 'voice-pick';
        pick.setAttribute('role', 'radio');
        pick.setAttribute('aria-checked', selected ? 'true' : 'false');
        pick.dataset.voice = voice.id;
        const mark = document.createElement('span');
        mark.className = 'voice-pick-mark';
        mark.innerHTML = icon('check');
        const text = document.createElement('span');
        text.className = 'voice-pick-text';
        const name = document.createElement('span');
        name.className = 'voice-pick-name';
        name.textContent = shortName(voice);
        const meta = document.createElement('span');
        meta.className = 'voice-pick-meta';
        meta.textContent = details(voice);
        text.append(name, meta);
        pick.append(mark, text);
        pick.addEventListener('click', () => choose(voice));

        const play = document.createElement('button');
        play.type = 'button';
        play.className = 'btn-secondary btn-panel-action voice-preview-btn';
        play.dataset.voice = voice.id;
        play.setAttribute('aria-label', `Probar la voz ${shortName(voice)}`);
        // Si se vuelve a pintar la lista (filtro) mientras suena esta voz, el botón nuevo lo refleja.
        const isPlaying = playingButton && playingButton.dataset.voice === voice.id && playingButton.dataset.mode !== 'idle';
        setPreviewButton(play, isPlaying ? playingButton.dataset.mode : 'idle');
        if (isPlaying) playingButton = play;
        play.addEventListener('click', () => preview(voice, play));

        row.append(pick, play);
        return row;
    }

    function setPreviewButton(button, mode) {
        const views = {
            idle: ['play', 'Probar'],
            loading: ['loader-circle', 'Cargando'],
            playing: ['square', 'Detener'],
        };
        const [iconName, label] = views[mode];
        button.dataset.mode = mode;
        button.disabled = mode === 'loading';
        button.classList.toggle('is-loading', mode === 'loading');
        button.innerHTML = `${icon(iconName)}<span>${label}</span>`;
    }

    // ── Acciones ───────────────────────────────────────────────

    async function choose(voice) {
        if (voice.id === state.current) return;
        const previous = state.current;
        state.current = voice.id;
        render();
        $('voice-catalog')?.querySelector(`.voice-pick[data-voice="${CSS.escape(voice.id)}"]`)?.focus();
        const ok = await window.settingsManager?.applyVoiceChoice?.(state.engine, state.provider, voice.id);
        if (ok) {
            setMeta(`Voz guardada: ${shortName(voice)}. Se usará en las próximas respuestas.`);
        } else {
            state.current = previous;
            render();
            setMeta('No se pudo guardar la voz. Revisa la conexión con el núcleo.');
        }
    }

    async function preview(voice, button) {
        const player = window.gminiTtsPlayer;
        if (button.dataset.mode === 'playing') {
            player.stop();
            return;
        }
        if (playingButton && playingButton !== button) setPreviewButton(playingButton, 'idle');
        playingButton = button;
        const key = `${state.engine}|${voice.id}`;
        try {
            let audio = previews.get(key);
            if (!audio) {
                setPreviewButton(button, 'loading');
                const data = await api('/voice/preview', {
                    method: 'POST',
                    body: JSON.stringify({ engine: state.engine, voice: voice.id }),
                });
                audio = data.audio_base64;
                if (!audio) throw new Error('el motor no devolvió audio');
                previews.set(key, audio);
            }
            if (playingButton !== button) return;
            setPreviewButton(button, 'playing');
            await player.preview(audio);
        } catch (err) {
            toast(`No se pudo probar la voz: ${err.message}`, true);
        } finally {
            // El botón pudo reemplazarse al repintar la lista: se limpia el vigente.
            const shown = playingButton && playingButton.dataset.voice === voice.id ? playingButton : button;
            if (shown.isConnected) setPreviewButton(shown, 'idle');
            if (playingButton === shown) playingButton = null;
        }
    }

    async function load() {
        const group = $('voice-catalog-group');
        const list = $('voice-catalog');
        if (!group || !list) return;
        const token = ++state.token;
        state.loading = true;
        list.setAttribute('aria-busy', 'true');
        setMeta('Cargando voces...');
        try {
            const params = new URLSearchParams({ engine: state.engine });
            if (state.provider === 'edge') params.set('locale', state.locale);
            const [catalog, config] = await Promise.all([
                api(`/voice/voices?${params}`),
                api('/config/voice').catch(() => null),
            ]);
            if (token !== state.token) return;
            state.voices = Array.isArray(catalog.voices) ? catalog.voices : [];
            state.current = String(config?.data?.voice?.[CONFIG_KEYS[state.provider]] || '');
            render();
            const chosen = state.voices.find((v) => v.id === state.current);
            setMeta(chosen
                ? `${state.voices.length} voces. En uso: ${shortName(chosen)}.`
                : `${state.voices.length} voces. Sin elegir: se usa la voz por defecto del motor.`);
        } catch (err) {
            if (token !== state.token) return;
            state.voices = [];
            render();
            setMeta(`No se pudieron cargar las voces: ${err.message}`);
        } finally {
            if (token === state.token) {
                state.loading = false;
                list.removeAttribute('aria-busy');
            }
        }
    }

    // ── API para Configuración ─────────────────────────────────

    function setEngine(engine, provider) {
        const group = $('voice-catalog-group');
        if (!group) return;
        const supported = Object.prototype.hasOwnProperty.call(CONFIG_KEYS, provider);
        group.hidden = !supported;
        const locale = $('voice-catalog-locale');
        if (locale) locale.closest('.voice-locale-field').hidden = provider !== 'edge';
        if (!supported) {
            state.engine = engine;
            state.provider = provider;
            return;
        }
        // Mismo motor ya cargado o cargándose: no se pide otra vez (la lista se repintaría).
        if (engine === state.engine && (state.voices.length || state.loading)) return;
        state.engine = engine;
        state.provider = provider;
        state.query = '';
        const filter = $('voice-catalog-filter');
        if (filter) filter.value = '';
        load();
    }

    function bind() {
        $('voice-catalog-filter')?.addEventListener('input', (e) => {
            state.query = e.target.value.trim().normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
            render();
        });
        $('voice-catalog-locale')?.addEventListener('change', (e) => {
            state.locale = e.target.value || 'es';
            state.voices = [];
            load();
        });
        // Radiogroup: flechas para moverse; Enter o espacio eligen (botones nativos).
        $('voice-catalog')?.addEventListener('keydown', (e) => {
            const radios = Array.from($('voice-catalog').querySelectorAll('.voice-pick'));
            const idx = radios.indexOf(document.activeElement);
            const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
            if (idx < 0 || !step) return;
            e.preventDefault();
            const next = radios[(idx + step + radios.length) % radios.length];
            radios.forEach((r) => { r.tabIndex = -1; });
            next.tabIndex = 0;
            next.focus();
        });
    }

    bind();
    window.gminiVoices = { setEngine, reload: load };
})();
