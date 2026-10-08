/**
 * G-Mini Agent — Configuración > Conectores.
 *
 * Conectores del núcleo (/api/connectors): fuentes de datos que el agente usa
 * como herramientas (clima, tipo de cambio, feriados, RSS, GitHub...). Cada
 * uno se activa por separado; sus ajustes van a la config y sus secretos al
 * keyring (PUT con un secreto vacío lo borra). "Probar" hace una consulta
 * real (POST /api/connectors/{id}/test). Se carga al abrir la página.
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const CATEGORIES = [
        ['productividad', 'Productividad'], ['informacion', 'Información'], ['finanzas', 'Finanzas'],
        ['desarrollo', 'Desarrollo'], ['notas', 'Notas'], ['general', 'General'],
    ];
    // Iconos Lucide que el núcleo puede pedir: los que el sprite no trae caen en "plug".
    const ICON_ALIASES = { github: 'git-branch', newspaper: 'rss', 'trending-up': 'chart-column', 'book-open-text': 'book-open' };
    const SPRITE_ICONS = new Set([
        'activity', 'bell', 'book-open', 'brain', 'calendar', 'calendar-days', 'chart-column', 'clipboard-list',
        'clock', 'cloud-sun', 'coins', 'database', 'file-text', 'folder', 'git-branch', 'globe', 'image',
        'key-round', 'landmark', 'link-2', 'list', 'message-square', 'music', 'network', 'package', 'plug',
        'plug-zap', 'rss', 'search', 'server', 'terminal', 'users', 'video', 'wrench', 'zap',
    ]);

    const state = { loaded: false, query: '', rawQuery: '', connectors: [], expanded: new Set() };
    const $ = (id) => document.getElementById(id);
    const icon = (name) => window.gminiDom.icon(name);
    const toast = (message, isError = false) => {
        if (typeof chatManager !== 'undefined') chatManager._toast(message, isError);
    };
    const norm = (text) => String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    /** Lanza solo con HTTP de error: /test responde 200 con {ok:false, message}. */
    async function api(path, options = {}) {
        const resp = await fetch(`${API}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!resp.ok) throw new Error(data?.detail || data?.error || `HTTP ${resp.status}`);
        return data || {};
    }

    function iconName(name) {
        const wanted = ICON_ALIASES[name] || name;
        return SPRITE_ICONS.has(wanted) ? wanted : 'plug';
    }

    function statusOf(c) {
        if (!c.enabled) return ['desactivado', 'is-muted'];
        if (!c.configured) return ['falta configurar', 'is-warn'];
        return ['listo', 'is-ok'];
    }

    function missing(c) {
        return (c.fields || [])
            .filter((f) => f.required && (f.secret ? !f.configured : !String(f.value ?? '').trim()))
            .map((f) => f.label || f.key);
    }

    // ── Carga y lista ──────────────────────────────────────────

    async function load() {
        const list = $('connectors-list');
        if (!list) return;
        list.setAttribute('aria-busy', 'true');
        try {
            const data = await api('/connectors');
            state.connectors = Array.isArray(data.connectors) ? data.connectors : [];
            renderList();
        } catch (err) {
            list.replaceChildren();
            const empty = el('div', 'memory-empty');
            empty.innerHTML = icon('plug');
            empty.appendChild(el('span', '', `No se pudieron leer los conectores: ${err.message}`));
            list.appendChild(empty);
            setSummary('');
        } finally {
            list.removeAttribute('aria-busy');
        }
    }

    function setSummary(text) {
        const summary = $('connectors-summary');
        if (summary) summary.textContent = text;
    }

    function summaryText() {
        const all = state.connectors;
        if (!all.length) return '';
        const active = all.filter((c) => c.enabled).length;
        const pending = all.filter((c) => c.enabled && !c.configured).length;
        return `${plural(all.length, 'conector', 'conectores')} · ${plural(active, 'activo', 'activos')}`
            + (pending ? ` · ${pending} por configurar` : '');
    }

    function matches(c) {
        if (!state.query) return true;
        const category = (CATEGORIES.find(([key]) => key === c.category) || [])[1] || '';
        const hay = norm([
            c.label, c.id, c.description, category,
            ...(c.actions || []).map((a) => `${a.name} ${a.description}`),
        ].join(' '));
        return state.query.split(/\s+/).every((word) => hay.includes(word));
    }

    function renderList() {
        const list = $('connectors-list');
        if (!list) return;
        list.replaceChildren();
        setSummary(summaryText());
        const shown = state.connectors.filter(matches);
        const known = new Set(CATEGORIES.map(([key]) => key));
        CATEGORIES.forEach(([key, label]) => {
            const group = shown.filter((c) => (known.has(c.category) ? c.category : 'general') === key);
            if (!group.length) return;
            const section = el('section', 'connector-group');
            const heading = el('h4', 'connector-group-title', label);
            heading.id = `connector-group-${key}`;
            section.setAttribute('aria-labelledby', heading.id);
            section.appendChild(heading);
            group.forEach((c) => section.appendChild(renderCard(c)));
            list.appendChild(section);
        });
        if (!list.children.length) {
            const empty = el('div', 'memory-empty');
            empty.innerHTML = icon('plug');
            empty.appendChild(el('span', '', state.query
                ? `Ningún conector coincide con "${state.rawQuery}".`
                : 'Esta versión del núcleo no trae conectores.'));
            list.appendChild(empty);
        }
    }

    // ── Tarjeta ────────────────────────────────────────────────

    function renderCard(c, flash = null) {
        const card = el('article', 'connector-card');
        card.dataset.id = c.id;
        const expanded = state.expanded.has(c.id);
        card.classList.toggle('is-off', !c.enabled);
        card.classList.toggle('expanded', expanded);

        const head = el('div', 'connector-head');
        const tile = el('span', 'connector-icon');
        tile.setAttribute('aria-hidden', 'true');
        tile.innerHTML = icon(iconName(c.icon));

        const main = el('div', 'connector-main');
        const title = el('div', 'connector-title');
        const name = el('h5', 'connector-name', c.label || c.id);
        name.id = `connector-name-${c.id}`;
        const [chipText, chipClass] = statusOf(c);
        title.append(name, el('span', `skill-chip ${chipClass}`, chipText));
        main.append(title, el('p', 'connector-desc', c.description || ''));

        const meta = el('div', 'connector-meta');
        const actions = Array.isArray(c.actions) ? c.actions : [];
        meta.appendChild(el('span', '', plural(actions.length, 'acción', 'acciones')));
        const secrets = (c.fields || []).filter((f) => f.secret);
        if (secrets.length) {
            const saved = secrets.some((f) => f.configured);
            const needed = secrets.some((f) => f.required);
            meta.appendChild(el('span', '', saved ? 'clave guardada' : (needed ? 'requiere clave' : 'clave opcional')));
        }
        if (actions.some((a) => a.writes)) meta.appendChild(el('span', '', 'puede modificar datos'));
        const lacking = c.enabled ? missing(c) : [];
        if (lacking.length) meta.appendChild(el('span', 'connector-meta-warn', `Falta: ${lacking.join(', ')}`));

        const more = el('button', 'connector-more');
        more.type = 'button';
        more.id = `connector-more-${c.id}`;
        more.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        more.setAttribute('aria-controls', `connector-body-${c.id}`);
        more.innerHTML = `${icon('chevron-right')}<span>${(c.fields || []).length ? 'Ajustes y prueba' : 'Detalles y prueba'}</span>`;
        meta.appendChild(more);
        main.appendChild(meta);

        const toggle = el('label', 'mcp-server-toggle connector-toggle');
        toggle.title = c.enabled ? 'Desactivar' : 'Activar';
        const enabled = el('input');
        enabled.type = 'checkbox';
        enabled.id = `connector-enabled-${c.id}`;
        enabled.checked = !!c.enabled;
        enabled.setAttribute('aria-label', `Activar ${c.label || c.id}`);
        enabled.addEventListener('change', () => setEnabled(c, enabled));
        toggle.append(enabled, el('span', 'toggle-track'));

        head.append(tile, main, toggle);

        const body = el('div', 'connector-body');
        body.id = `connector-body-${c.id}`;
        body.setAttribute('role', 'region');
        body.setAttribute('aria-labelledby', name.id);
        body.hidden = !expanded;
        if (expanded) fillBody(c, body, flash);

        more.addEventListener('click', () => {
            const open = !card.classList.contains('expanded');
            card.classList.toggle('expanded', open);
            more.setAttribute('aria-expanded', open ? 'true' : 'false');
            body.hidden = !open;
            if (open) {
                state.expanded.add(c.id);
                if (!body.childElementCount) fillBody(c, body);
            } else {
                state.expanded.delete(c.id);
            }
        });

        card.append(head, body);
        return card;
    }

    function fillBody(c, body, flash = null) {
        body.replaceChildren();
        if ((c.fields || []).length) body.appendChild(renderFields(c, flash));
        body.appendChild(renderTest(c));
        const actions = Array.isArray(c.actions) ? c.actions : [];
        if (actions.length) {
            const box = el('div', 'connector-section');
            box.appendChild(el('span', 'connector-section-label', 'Qué puede hacer el agente'));
            const list = el('ul', 'connector-actions');
            actions.forEach((action) => {
                const item = el('li', 'connector-action');
                item.appendChild(el('code', '', action.name));
                item.appendChild(el('span', '', action.description || ''));
                const params = Object.keys(action.params || {});
                if (params.length) item.appendChild(el('span', 'connector-action-params', params.join(', ')));
                if (action.writes) item.appendChild(el('span', 'skill-chip is-warn', 'modifica datos'));
                list.appendChild(item);
            });
            box.appendChild(list);
            body.appendChild(box);
        }
        if (/^https?:\/\//i.test(c.docs_url || '')) {
            const docs = el('a', 'connector-docs');
            docs.href = c.docs_url;
            docs.target = '_blank';
            docs.rel = 'noopener noreferrer';
            docs.innerHTML = `${icon('external-link')}<span>Documentación del servicio</span>`;
            body.appendChild(docs);
        }
    }

    // ── Ajustes ────────────────────────────────────────────────

    const textOf = (value) => (value == null ? '' : (typeof value === 'string' ? value : JSON.stringify(value)));

    function renderFields(c, flash) {
        const form = el('form', 'connector-section connector-fields');
        form.noValidate = true;
        form.appendChild(el('span', 'connector-section-label', 'Ajustes'));
        const controls = [];

        (c.fields || []).forEach((field) => {
            const id = `cf-${c.id}-${field.key}`;
            const wrap = el('div', 'connector-field');
            const label = el('label', '', field.label || field.key);
            label.htmlFor = id;
            if (field.secret) {
                const [text, cls] = field.configured ? ['guardada', 'is-ok'] : (field.required ? ['falta', 'is-warn'] : ['opcional', 'is-muted']);
                label.appendChild(el('span', `skill-chip ${cls}`, text));
            } else if (field.required) {
                label.appendChild(el('span', 'skill-chip is-warn', 'obligatorio'));
            }

            let control;
            if (field.kind === 'textarea') {
                control = el('textarea', 'setting-textarea');
                control.rows = 3;
                control.value = textOf(field.value);
            } else if (field.kind === 'bool') {
                control = el('input');
                control.type = 'checkbox';
                control.checked = field.value === true || field.value === 'true';
            } else {
                control = el('input', field.secret ? 'api-key-input' : 'setting-input');
                control.type = field.secret ? 'password' : (field.kind === 'url' ? 'url' : 'text');
                control.value = field.secret ? '' : textOf(field.value);
            }
            control.id = id;
            control.name = field.key;
            if (control.type !== 'checkbox') {
                control.autocomplete = 'off';
                control.spellcheck = false;
                control.placeholder = field.secret && field.configured
                    ? 'Guardada. Escribe otra para reemplazarla'
                    : String(field.placeholder || '');
            }
            wrap.appendChild(label);
            appendControl(wrap, c, field, control);
            if (field.help) {
                const help = el('span', 'setting-help-text', field.help);
                help.id = `${id}-help`;
                control.setAttribute('aria-describedby', help.id);
                wrap.appendChild(help);
            }
            controls.push({ field, control });
            form.appendChild(wrap);
        });

        const row = el('div', 'connector-buttons');
        const save = el('button', 'btn-primary-accent');
        save.type = 'submit';
        save.innerHTML = `${icon('check')}<span>Guardar ajustes</span>`;
        save.disabled = true;
        const status = el('span', 'connector-status');
        status.setAttribute('role', 'status');
        if (flash) status.textContent = flash;
        row.append(save, status);
        form.appendChild(row);

        const changes = () => {
            const payload = {};
            controls.forEach(({ field, control }) => {
                if (field.secret) {
                    if (control.value.trim()) payload[field.key] = control.value.trim();
                } else if (control.type === 'checkbox') {
                    if (control.checked !== (field.value === true || field.value === 'true')) payload[field.key] = control.checked;
                } else if (control.value !== textOf(field.value)) {
                    payload[field.key] = field.kind === 'textarea' ? control.value.replace(/\s+$/, '') : control.value.trim();
                }
            });
            return payload;
        };
        const refreshSave = () => {
            save.disabled = !Object.keys(changes()).length;
            if (!save.disabled) status.textContent = '';
        };
        form.addEventListener('input', refreshSave);
        form.addEventListener('change', refreshSave);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const payload = changes();
            if (!Object.keys(payload).length) return;
            save.disabled = true;
            status.classList.remove('is-error');
            status.textContent = 'Guardando...';
            try {
                const data = await api(`/connectors/${encodeURIComponent(c.id)}`, { method: 'PUT', body: JSON.stringify(payload) });
                replaceConnector(data.connector, 'Ajustes guardados.');
            } catch (err) {
                status.classList.add('is-error');
                status.textContent = `No se pudo guardar: ${err.message}`;
                save.disabled = false;
            }
        });
        return form;
    }

    /** Un secreto guardado lleva al lado "Quitar" (PUT con valor vacío lo borra del keyring). */
    function appendControl(wrap, c, field, control) {
        if (control.type === 'checkbox') {
            const toggle = el('label', 'mcp-server-toggle');
            toggle.append(control, el('span', 'toggle-track'));
            wrap.appendChild(toggle);
            return;
        }
        if (!(field.secret && field.configured)) {
            wrap.appendChild(control);
            return;
        }
        const row = el('div', 'connector-secret-row');
        const clear = el('button', 'btn-secondary btn-panel-action');
        clear.type = 'button';
        clear.innerHTML = `${icon('trash-2')}<span>Quitar</span>`;
        clear.setAttribute('aria-label', `Quitar ${field.label || field.key}`);
        clear.addEventListener('click', async () => {
            clear.disabled = true;
            try {
                const data = await api(`/connectors/${encodeURIComponent(c.id)}`, {
                    method: 'PUT', body: JSON.stringify({ [field.key]: '' }),
                });
                replaceConnector(data.connector, `${field.label || field.key}: eliminada del keyring.`);
            } catch (err) {
                clear.disabled = false;
                toast(`No se pudo quitar: ${err.message}`, true);
            }
        });
        row.append(control, clear);
        wrap.appendChild(row);
    }

    // ── Prueba ─────────────────────────────────────────────────

    function renderTest(c) {
        const box = el('div', 'connector-section');
        const row = el('div', 'connector-buttons');
        const button = el('button', 'btn-secondary btn-panel-action');
        button.type = 'button';
        const idle = () => {
            button.disabled = false;
            button.classList.remove('is-loading');
            button.innerHTML = `${icon('plug-zap')}<span>Probar conexión</span>`;
        };
        idle();
        const result = el('div', 'connector-result');
        result.setAttribute('role', 'status');
        result.hidden = true;
        button.addEventListener('click', async () => {
            button.disabled = true;
            button.classList.add('is-loading');
            button.innerHTML = `${icon('loader-circle')}<span>Probando...</span>`;
            result.hidden = true;
            let ok = false;
            let message = '';
            try {
                const data = await api(`/connectors/${encodeURIComponent(c.id)}/test`, { method: 'POST' });
                ok = !!data.ok;
                message = data.message || (ok ? 'Responde correctamente.' : 'No respondió.');
            } catch (err) {
                message = err.message;
            }
            result.className = `connector-result ${ok ? 'is-ok' : 'is-error'}`;
            result.innerHTML = icon(ok ? 'circle-check' : 'circle-alert');
            result.appendChild(el('span', '', message));
            result.hidden = false;
            idle();
        });
        row.appendChild(button);
        if (!c.enabled) row.appendChild(el('span', 'connector-status', 'Desactivado: el agente no lo usa, pero puedes probarlo.'));
        box.append(row, result);
        return box;
    }

    // ── Cambios ────────────────────────────────────────────────

    async function setEnabled(c, input) {
        input.disabled = true;
        try {
            const data = await api(`/connectors/${encodeURIComponent(c.id)}`, {
                method: 'PUT', body: JSON.stringify({ enabled: input.checked }),
            });
            replaceConnector(data.connector);
        } catch (err) {
            input.checked = !input.checked;
            input.disabled = false;
            toast(`No se pudo cambiar el conector: ${err.message}`, true);
        }
    }

    /** Repinta una tarjeta con el estado que devolvió el núcleo, sin perder el foco ni lo desplegado. */
    function replaceConnector(updated, flash = null) {
        if (!updated?.id) return;
        const index = state.connectors.findIndex((c) => c.id === updated.id);
        if (index >= 0) state.connectors[index] = updated;
        else state.connectors.push(updated);
        const focusedId = document.activeElement?.id || '';
        const card = $('connectors-list')?.querySelector(`.connector-card[data-id="${CSS.escape(updated.id)}"]`);
        if (!card) {
            renderList();
            return;
        }
        card.replaceWith(renderCard(updated, flash));
        const next = focusedId ? $(focusedId) : null;
        if (next && !next.disabled) next.focus();
        else if (flash) $(`connector-more-${updated.id}`)?.focus();
        setSummary(summaryText());  // activos y por configurar también cambian
    }

    // ── Arranque ───────────────────────────────────────────────

    $('connectors-search')?.addEventListener('input', (e) => {
        state.rawQuery = e.target.value.trim();
        state.query = norm(state.rawQuery);
        renderList();
    });

    document.addEventListener('gmini:settings-page', (e) => {
        if (e.detail?.page !== 'connectors' || !e.detail.open || state.loaded) return;
        state.loaded = true;
        load();
    });

    window.gminiConnectors = { reload: load };
})();
