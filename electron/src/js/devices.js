/**
 * G-Mini Agent — Configuración > Dispositivos > Otros G-Mini.
 *
 * Otros equipos con G-Mini (un VPS, una Raspberry Pi, otra PC) a los que este
 * delega tareas. Se emparejan con la dirección del otro equipo y el código de
 * 6 dígitos que muestra (POST /api/remote-servers/pair); el token queda en el
 * keyring del núcleo. Desde aquí se comprueba si responden, se les manda una
 * tarea y se quitan. El núcleo responde los errores como {ok:false, error}
 * con 502 (el otro equipo no contestó) o 404 (no está emparejado).
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const TASK_STATES = {
        done: ['terminada', 'is-ok'],
        failed: ['falló', 'is-warn'],
        cancelled: ['cancelada', 'is-muted'],
    };
    const state = { loaded: false, servers: [], status: new Map(), open: new Set() };
    const $ = (id) => document.getElementById(id);
    const icon = (name) => window.gminiDom.icon(name);
    const toast = (message, isError = false) => {
        if (typeof chatManager !== 'undefined') chatManager._toast(message, isError);
    };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    async function api(path, options = {}) {
        const resp = await fetch(`${API}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!resp.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${resp.status}`);
        return data || {};
    }

    function pairedOn(epochSeconds) {
        const value = Number(epochSeconds);
        if (!Number.isFinite(value) || value <= 0) return '';
        return new Date(value * 1000).toLocaleDateString('es', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    // ── Lista ──────────────────────────────────────────────────

    async function load() {
        const list = $('remote-servers-list');
        if (!list) return;
        list.setAttribute('aria-busy', 'true');
        try {
            const data = await api('/remote-servers');
            state.servers = Array.isArray(data.servers) ? data.servers : [];
            const ids = new Set(state.servers.map((s) => s.id));
            Array.from(state.status.keys()).forEach((id) => { if (!ids.has(id)) state.status.delete(id); });
            render();
            state.servers.forEach((server) => checkStatus(server.id));
        } catch (err) {
            list.replaceChildren(emptyState(`No se pudo leer la lista: ${err.message}`));
        } finally {
            list.removeAttribute('aria-busy');
        }
    }

    function emptyState(text) {
        const empty = el('div', 'memory-empty');
        empty.innerHTML = icon('network');
        empty.appendChild(el('span', '', text));
        return empty;
    }

    function render() {
        const list = $('remote-servers-list');
        if (!list) return;
        list.replaceChildren();
        if (!state.servers.length) {
            list.appendChild(emptyState('Aún no hay otros G-Mini emparejados. Con uno en un VPS, una Raspberry Pi u otra PC, el agente puede mandarle tareas.'));
        }
        state.servers.forEach((server) => list.appendChild(renderCard(server)));
        const refresh = $('btn-remote-refresh');
        if (refresh) refresh.hidden = !state.servers.length;
    }

    function updateCard(id) {
        const server = state.servers.find((s) => s.id === id);
        const card = $('remote-servers-list')?.querySelector(`.remote-card[data-id="${CSS.escape(id)}"]`);
        if (!server || !card) return;
        // Solo la cabecera: el formulario de tarea (y lo que se esté escribiendo) se conserva.
        const focused = card.querySelector('.connector-head')?.contains(document.activeElement) ? document.activeElement.id : '';
        card.querySelector('.connector-head').replaceWith(renderHead(server));
        if (focused) $(focused)?.focus();
    }

    // ── Tarjeta ────────────────────────────────────────────────

    function statusView(id) {
        const info = state.status.get(id) || { state: 'checking' };
        if (info.state === 'online') {
            const health = info.health || {};
            const parts = [];
            if (health.version) parts.push(`versión ${health.version}`);
            if (health.mode) parts.push(health.mode === 'server' ? 'servidor' : 'escritorio');
            const scopes = Array.isArray(info.me?.scopes) ? info.me.scopes : null;
            const warn = scopes && !scopes.includes('tasks') ? 'Sin permiso para tareas: vuelve a emparejarlo.' : '';
            return { chip: ['en línea', 'is-ok'], detail: parts.join(' · '), warn, mode: health.mode };
        }
        if (info.state === 'offline') return { chip: ['sin conexión', 'is-warn'], detail: '', warn: info.error || 'No respondió.' };
        return { chip: ['comprobando', 'is-muted'], detail: '', warn: '' };
    }

    function renderCard(server) {
        const card = el('article', 'connector-card remote-card');
        card.dataset.id = server.id;
        const open = state.open.has(server.id);
        card.classList.toggle('expanded', open);
        const body = el('div', 'connector-body');
        body.id = `remote-body-${server.id}`;
        body.hidden = !open;
        body.setAttribute('role', 'region');
        body.setAttribute('aria-label', `Enviar una tarea a ${server.name}`);
        body.appendChild(renderTaskForm(server));
        card.append(renderHead(server), body);
        return card;
    }

    function renderHead(server) {
        const view = statusView(server.id);
        const head = el('div', 'connector-head');
        const tile = el('span', 'connector-icon');
        tile.setAttribute('aria-hidden', 'true');
        tile.innerHTML = icon(view.mode === 'desktop' ? 'laptop' : 'server');

        const main = el('div', 'connector-main');
        const title = el('div', 'connector-title');
        title.append(el('h5', 'connector-name', server.name || server.url), el('span', `skill-chip ${view.chip[1]}`, view.chip[0]));
        const desc = el('p', 'connector-desc remote-url');
        desc.appendChild(el('code', '', server.url));
        if (server.agent_name) desc.append(` · agente ${server.agent_name}`);
        main.append(title, desc);

        const meta = el('div', 'connector-meta');
        if (view.detail) meta.appendChild(el('span', '', view.detail));
        const date = pairedOn(server.added_at);
        if (date) meta.appendChild(el('span', '', `emparejado el ${date}`));
        if (view.warn) {
            const warn = el('span', 'connector-meta-warn remote-warn', view.warn);
            warn.title = view.warn;
            meta.appendChild(warn);
        }
        const more = el('button', 'connector-more');
        more.type = 'button';
        more.id = `remote-more-${server.id}`;
        more.setAttribute('aria-expanded', state.open.has(server.id) ? 'true' : 'false');
        more.setAttribute('aria-controls', `remote-body-${server.id}`);
        more.innerHTML = `${icon('chevron-right')}<span>Enviar tarea</span>`;
        more.addEventListener('click', () => toggleTask(server.id));
        meta.appendChild(more);
        main.appendChild(meta);

        const actions = el('div', 'remote-actions');
        const check = el('button', 'btn-icon');
        check.type = 'button';
        check.id = `remote-check-${server.id}`;
        check.innerHTML = icon('refresh-cw');
        check.setAttribute('aria-label', `Comprobar ${server.name}`);
        check.title = 'Comprobar conexión';
        // aria-disabled y no disabled: así el botón conserva el foco mientras comprueba.
        const checking = (state.status.get(server.id) || { state: 'checking' }).state === 'checking';
        check.setAttribute('aria-disabled', checking ? 'true' : 'false');
        check.classList.toggle('is-loading', checking);
        check.addEventListener('click', () => checkStatus(server.id));
        actions.append(check, removeButton(server));

        head.append(tile, main, actions);
        return head;
    }

    function toggleTask(id) {
        const card = $('remote-servers-list')?.querySelector(`.remote-card[data-id="${CSS.escape(id)}"]`);
        if (!card) return;
        const open = !card.classList.contains('expanded');
        card.classList.toggle('expanded', open);
        card.querySelector('.connector-body').hidden = !open;
        $(`remote-more-${id}`)?.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            state.open.add(id);
            $(`remote-task-${id}`)?.focus();
        } else {
            state.open.delete(id);
        }
    }

    /** Pide un segundo clic antes de quitarlo: hay que volver a emparejarlo con un código nuevo. */
    function removeButton(server) {
        const button = el('button', 'btn-secondary btn-panel-action');
        button.type = 'button';
        const idle = () => {
            button.classList.remove('btn-danger');
            button.innerHTML = `${icon('unlink')}<span>Quitar</span>`;
            button.setAttribute('aria-label', `Quitar ${server.name}`);
        };
        idle();
        button.addEventListener('click', async () => {
            if (!button.classList.contains('btn-danger')) {
                button.classList.add('btn-danger');
                button.innerHTML = `${icon('triangle-alert')}<span>Confirmar</span>`;
                button.setAttribute('aria-label', `Confirmar: quitar ${server.name}`);
                clearTimeout(button._timer);
                button._timer = setTimeout(idle, 4000);
                return;
            }
            clearTimeout(button._timer);
            button.disabled = true;
            try {
                await api(`/remote-servers/${encodeURIComponent(server.id)}`, { method: 'DELETE' });
                state.open.delete(server.id);
                state.status.delete(server.id);
                toast(`Se quitó ${server.name}. Para volver a usarlo, emparéjalo otra vez.`);
                await load();
            } catch (err) {
                button.disabled = false;
                idle();
                toast(`No se pudo quitar: ${err.message}`, true);
            }
        });
        return button;
    }

    async function checkStatus(id) {
        if (state.status.get(id)?.pending) return;
        state.status.set(id, { state: 'checking', pending: true });
        updateCard(id);
        try {
            const data = await api(`/remote-servers/${encodeURIComponent(id)}/status`);
            state.status.set(id, { state: 'online', health: data.health || {}, me: data.me || {} });
        } catch (err) {
            state.status.set(id, { state: 'offline', error: err.message });
        }
        updateCard(id);
    }

    // ── Tareas ─────────────────────────────────────────────────

    function renderTaskForm(server) {
        const form = el('form', 'connector-section remote-task');
        form.noValidate = true;
        const label = el('label', 'connector-section-label', `Tarea para ${server.name}`);
        const prompt = el('textarea', 'setting-textarea is-prose');
        prompt.id = `remote-task-${server.id}`;
        label.htmlFor = prompt.id;
        prompt.rows = 3;
        prompt.placeholder = 'Ej.: revisa el espacio en disco y avísame si queda menos del 10 %';
        const waitLabel = el('label', 'remote-wait');
        const wait = el('input');
        wait.type = 'checkbox';
        waitLabel.append(wait, el('span', '', 'Esperar el resultado aquí (hasta 5 minutos)'));
        const row = el('div', 'connector-buttons');
        const send = el('button', 'btn-primary-accent');
        send.type = 'submit';
        send.innerHTML = `${icon('send')}<span>Enviar</span>`;
        const status = el('span', 'connector-status');
        status.setAttribute('role', 'status');
        row.append(send, status);
        const result = el('div', 'remote-result');
        result.hidden = true;
        form.append(label, prompt, waitLabel, row, result);

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const text = prompt.value.trim();
            if (!text) {
                status.classList.add('is-error');
                status.textContent = 'Escribe qué tiene que hacer.';
                prompt.focus();
                return;
            }
            send.disabled = true;
            send.classList.add('is-loading');
            send.innerHTML = `${icon('loader-circle')}<span>${wait.checked ? 'Esperando...' : 'Enviando...'}</span>`;
            status.classList.remove('is-error');
            status.textContent = wait.checked ? `${server.name} está trabajando en la tarea.` : '';
            result.hidden = true;
            try {
                const data = await api(`/remote-servers/${encodeURIComponent(server.id)}/tasks`, {
                    method: 'POST', body: JSON.stringify({ prompt: text, wait: wait.checked }),
                });
                showResult(result, data.task || {});
                status.textContent = '';
                prompt.value = '';
            } catch (err) {
                status.classList.add('is-error');
                status.textContent = `No se pudo enviar: ${err.message}`;
            } finally {
                send.disabled = false;
                send.classList.remove('is-loading');
                send.innerHTML = `${icon('send')}<span>Enviar</span>`;
            }
        });
        return form;
    }

    function showResult(box, task) {
        box.replaceChildren();
        const [label, cls] = TASK_STATES[task.status] || ['en curso', 'is-muted'];
        const head = el('div', 'remote-result-head');
        head.append(el('span', `skill-chip ${cls}`, label));
        if (task.task_id) head.appendChild(el('code', '', task.task_id));
        box.appendChild(head);
        const text = task.error || task.result || task.note
            || (task.status === 'done' ? 'Terminó sin devolver texto.' : 'Quedó en cola. El agente puede consultar cómo va cuando se lo pidas.');
        const body = el('div', `remote-result-text${task.error ? ' is-error' : ''}`, String(text));
        box.appendChild(body);
        box.hidden = false;
    }

    // ── Emparejar ──────────────────────────────────────────────

    async function pair(e) {
        e.preventDefault();
        const url = $('remote-pair-url');
        const code = $('remote-pair-code');
        const name = $('remote-pair-name');
        const button = $('btn-remote-pair');
        const status = $('remote-pair-status');
        const fail = (message, field) => {
            status.classList.add('is-error');
            status.textContent = message;
            field?.focus();
        };
        status.classList.remove('is-error');
        if (!url.value.trim()) return fail('Escribe la dirección del otro equipo.', url);
        const digits = code.value.replace(/\D/g, '');
        if (digits.length !== 6) return fail('El código tiene 6 dígitos.', code);

        button.disabled = true;
        status.textContent = `Conectando con ${url.value.trim()}...`;
        try {
            const data = await api('/remote-servers/pair', {
                method: 'POST',
                body: JSON.stringify({ url: url.value.trim(), code: digits, name: name.value.trim() }),
            });
            const server = data.server || {};
            status.textContent = `Emparejado con ${server.name || url.value.trim()}.`;
            toast(`Emparejado con ${server.name || url.value.trim()}. El agente ya puede mandarle tareas.`);
            url.value = '';
            code.value = '';
            name.value = '';
            await load();
        } catch (err) {
            fail(err.message, code);
        } finally {
            button.disabled = false;
        }
    }

    // ── Arranque ───────────────────────────────────────────────

    $('remote-pair-form')?.addEventListener('submit', pair);
    // Solo dígitos, como "482 913" pegado desde otro lado.
    $('remote-pair-code')?.addEventListener('input', (e) => {
        const digits = e.target.value.replace(/\D/g, '').slice(0, 6);
        if (digits !== e.target.value) e.target.value = digits;
    });
    $('btn-remote-refresh')?.addEventListener('click', () => state.servers.forEach((s) => checkStatus(s.id)));

    document.addEventListener('gmini:settings-page', (e) => {
        if (e.detail?.page !== 'devices' || !e.detail.open) return;
        if (!state.loaded) {
            state.loaded = true;
            load();
        }
    });

    window.gminiDevices = { reload: load };
})();
