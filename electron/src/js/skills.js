/**
 * G-Mini Agent — Configuración > Skills.
 *
 * Skills SKILL.md del núcleo (/api/agent-skills): instaladas por pack,
 * incluidas con la app y aprendidas por el agente. Se instalan desde GitHub
 * (owner/repo o URL) o una carpeta local; se activan, se fijan (solo las del
 * agente), se borran y se pasa el curador. Las skills con tools declaran
 * variables de entorno que se guardan en el keyring (/api/skills/{id}/env).
 * Se carga al abrir la página por primera vez.
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const TABS = {
        installed: { label: 'Instaladas', empty: 'Aún no instalaste ninguna skill. Prueba con una de las fuentes sugeridas.' },
        bundled: { label: 'Incluidas', empty: 'Esta versión no trae skills incluidas.' },
        agent: { label: 'Aprendidas', empty: 'El agente todavía no escribió skills propias.' },
    };
    const LIFECYCLE = {
        active: ['activa', 'is-ok'],
        stale: ['sin uso', 'is-warn'],
        archived: ['archivada', 'is-muted'],
    };

    const state = { loaded: false, tab: 'installed', query: '', skills: [], packs: [], suggested: [] };
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
        if (!resp.ok) throw new Error(data?.detail || data?.error || `HTTP ${resp.status}`);
        return data || {};
    }

    function relativeDate(epochSeconds) {
        const value = Number(epochSeconds);
        if (!Number.isFinite(value) || value <= 0) return '';
        const days = Math.floor((Date.now() / 1000 - value) / 86400);
        if (days <= 0) return 'hoy';
        if (days === 1) return 'ayer';
        if (days < 30) return `hace ${days} días`;
        return new Date(value * 1000).toLocaleDateString('es', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    /** Botón que pide un segundo clic antes de una acción destructiva. */
    function confirmButton(label, confirmLabel, onConfirm) {
        const button = el('button', 'btn-secondary btn-panel-action');
        button.type = 'button';
        const idle = () => {
            button.classList.remove('btn-danger');
            button.innerHTML = `${icon('trash-2')}<span>${label}</span>`;
        };
        idle();
        button.addEventListener('click', async () => {
            if (!button.classList.contains('btn-danger')) {
                button.classList.add('btn-danger');
                button.innerHTML = `${icon('triangle-alert')}<span>${confirmLabel}</span>`;
                clearTimeout(button._timer);
                button._timer = setTimeout(idle, 4000);
                return;
            }
            clearTimeout(button._timer);
            button.disabled = true;
            try {
                await onConfirm();
            } finally {
                button.disabled = false;
                idle();
            }
        });
        return button;
    }

    // ── Carga ──────────────────────────────────────────────────

    async function load() {
        const list = $('skills-list');
        list?.setAttribute('aria-busy', 'true');
        try {
            const data = await api('/agent-skills');
            state.skills = Array.isArray(data.skills) ? data.skills : [];
            state.packs = Array.isArray(data.packs) ? data.packs : [];
            state.suggested = Array.isArray(data.suggested) ? data.suggested : [];
            renderSuggested();
            renderTabs();
            renderList();
        } catch (err) {
            if (list) {
                list.innerHTML = '';
                list.appendChild(el('div', 'memory-empty', `No se pudieron leer las skills: ${err.message}`));
            }
        } finally {
            list?.removeAttribute('aria-busy');
        }
    }

    // ── Instalar ───────────────────────────────────────────────

    function installedSources() {
        return new Set(state.packs.map((p) => String(p.source || '').replace(/\/+$/, '').toLowerCase()));
    }

    function renderSuggested() {
        const box = $('skills-suggested');
        if (!box) return;
        box.innerHTML = '';
        const installed = installedSources();
        state.suggested.forEach((source) => {
            const card = el('div', 'skill-source');
            const text = el('div', 'skill-source-text');
            text.append(el('span', 'skill-source-label', source.label || source.url), el('span', 'skill-source-desc', source.description || ''));
            const button = el('button', 'btn-secondary btn-panel-action');
            button.type = 'button';
            const done = installed.has(String(source.url || '').replace(/\/+$/, '').toLowerCase());
            button.disabled = done;
            button.innerHTML = done ? `${icon('check')}<span>Instalado</span>` : `${icon('download')}<span>Instalar</span>`;
            button.addEventListener('click', () => install(source.url, button));
            card.append(text, button);
            box.appendChild(card);
        });
    }

    async function install(source, trigger) {
        const status = $('skills-install-status');
        const value = String(source || '').trim();
        if (!value) {
            if (status) status.textContent = 'Escribe owner/repo, una URL de GitHub o la ruta de una carpeta.';
            $('skills-install-source')?.focus();
            return;
        }
        const buttons = [trigger, $('btn-skills-install')].filter(Boolean);
        buttons.forEach((b) => { b.disabled = true; });
        if (status) {
            status.classList.remove('is-error');
            status.textContent = `Instalando desde ${value}...`;
        }
        try {
            const result = await api('/agent-skills/install', { method: 'POST', body: JSON.stringify({ source: value }) });
            const count = Array.isArray(result.installed) ? result.installed.length : 0;
            const skipped = Array.isArray(result.skipped) ? result.skipped.length : 0;
            const message = `Pack "${result.pack || value}": ${count} skills instaladas${skipped ? `, ${skipped} omitidas` : ''}.`;
            if (status) status.textContent = message;
            toast(message);
            const input = $('skills-install-source');
            if (input) input.value = '';
            state.tab = 'installed';
            await load();
        } catch (err) {
            if (status) {
                status.classList.add('is-error');
                status.textContent = `No se pudo instalar: ${err.message}`;
            }
        } finally {
            buttons.forEach((b) => { b.disabled = false; });
            renderSuggested();
        }
    }

    // ── Lista por pestañas ─────────────────────────────────────

    function bySource(source) {
        return state.skills.filter((s) => (s.source || 'installed') === source);
    }

    function renderTabs() {
        const bar = $('skills-tabs');
        if (!bar) return;
        bar.innerHTML = '';
        Object.entries(TABS).forEach(([key, info]) => {
            const tab = el('button', `mcp-tab${state.tab === key ? ' active' : ''}`);
            tab.type = 'button';
            tab.id = `skills-tab-${key}`;
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', state.tab === key ? 'true' : 'false');
            tab.setAttribute('aria-controls', 'skills-list');
            tab.tabIndex = state.tab === key ? 0 : -1;
            tab.append(info.label, el('span', 'skills-tab-count', String(bySource(key).length)));
            tab.addEventListener('click', () => selectTab(key));
            bar.appendChild(tab);
        });
        $('skills-list')?.setAttribute('aria-labelledby', `skills-tab-${state.tab}`);
    }

    function selectTab(key, focus = false) {
        state.tab = key;
        renderTabs();
        renderList();
        if (focus) $(`skills-tab-${key}`)?.focus();
    }

    function matches(skill) {
        if (!state.query) return true;
        const hay = `${skill.name} ${skill.description} ${skill.pack}`.toLowerCase();
        return state.query.toLowerCase().split(' ').filter(Boolean).every((w) => hay.includes(w));
    }

    function renderList() {
        const list = $('skills-list');
        if (!list) return;
        list.innerHTML = '';
        const skills = bySource(state.tab).filter(matches);
        if (state.tab === 'installed') {
            renderPacks(list, skills);
        } else {
            skills.forEach((skill) => list.appendChild(renderSkill(skill)));
        }
        if (!list.children.length) {
            const empty = el('div', 'memory-empty');
            empty.innerHTML = icon('wrench');
            empty.appendChild(el('span', '', state.query ? `Ninguna skill coincide con "${state.query}".` : TABS[state.tab].empty));
            list.appendChild(empty);
        }
    }

    function renderPacks(list, skills) {
        const byPack = new Map();
        skills.forEach((skill) => {
            const key = skill.pack || 'Sin pack';
            if (!byPack.has(key)) byPack.set(key, []);
            byPack.get(key).push(skill);
        });
        byPack.forEach((packSkills, packName) => {
            const info = state.packs.find((p) => p.pack === packName) || {};
            const section = el('section', 'skill-pack');
            const head = el('div', 'skill-pack-head');
            const title = el('div', 'skill-pack-title');
            title.append(el('span', 'skill-pack-name', packName));
            const meta = [info.source, info.installed_at ? `instalado ${String(info.installed_at).slice(0, 10)}` : '']
                .filter(Boolean).join(' · ');
            if (meta) title.append(el('span', 'skill-pack-meta', meta));
            head.appendChild(title);
            if (info.pack) {
                head.appendChild(confirmButton('Quitar pack', 'Confirmar', async () => {
                    try {
                        await api(`/agent-skills/packs/${encodeURIComponent(info.pack)}`, { method: 'DELETE' });
                        toast(`Pack "${info.pack}" eliminado.`);
                        await load();
                    } catch (err) {
                        toast(`No se pudo quitar el pack: ${err.message}`, true);
                    }
                }));
            }
            section.appendChild(head);
            packSkills.forEach((skill) => section.appendChild(renderSkill(skill)));
            list.appendChild(section);
        });
    }

    function renderSkill(skill) {
        const row = el('article', 'skill-row');
        const main = el('div', 'skill-main');
        const head = el('div', 'skill-head');
        head.append(el('span', 'skill-name', skill.name));
        if (skill.source === 'agent') {
            const [label, cls] = LIFECYCLE[skill.lifecycle] || LIFECYCLE.active;
            head.append(el('span', `skill-chip ${cls}`, label));
            if (skill.pinned) head.append(el('span', 'skill-chip is-accent', 'fijada'));
        }
        main.append(head, el('p', 'skill-desc', skill.description || 'Sin descripción.'));
        const meta = [];
        if (Number(skill.uses) > 0) meta.push(`${skill.uses} usos`);
        const last = relativeDate(skill.last_used_at);
        if (last) meta.push(`último uso ${last}`);
        if (Array.isArray(skill.resources) && skill.resources.length) meta.push(`${skill.resources.length} recursos`);
        if (meta.length) main.append(el('span', 'skill-meta', meta.join(' · ')));

        const actions = el('div', 'skill-actions');
        if (skill.source === 'agent') {
            const pin = el('button', 'btn-icon');
            pin.type = 'button';
            pin.innerHTML = icon('pin');
            pin.setAttribute('aria-pressed', skill.pinned ? 'true' : 'false');
            pin.setAttribute('aria-label', skill.pinned ? 'Desfijar skill' : 'Fijar skill (el curador no la archiva)');
            pin.title = pin.getAttribute('aria-label');
            pin.addEventListener('click', async () => {
                try {
                    await api(`/agent-skills/${encodeURIComponent(skill.name)}/pinned`, {
                        method: 'POST', body: JSON.stringify({ pinned: !skill.pinned }),
                    });
                    await load();
                } catch (err) {
                    toast(`No se pudo cambiar: ${err.message}`, true);
                }
            });
            actions.appendChild(pin);
        }
        const toggle = el('label', 'mcp-server-toggle');
        toggle.title = skill.enabled ? 'Desactivar' : 'Activar';
        const input = el('input');
        input.type = 'checkbox';
        input.checked = skill.enabled !== false;
        input.setAttribute('aria-label', `Activar ${skill.name}`);
        input.addEventListener('change', async () => {
            try {
                await api(`/agent-skills/${encodeURIComponent(skill.name)}/enabled`, {
                    method: 'POST', body: JSON.stringify({ enabled: input.checked }),
                });
                skill.enabled = input.checked;
            } catch (err) {
                input.checked = !input.checked;
                toast(`No se pudo cambiar la skill: ${err.message}`, true);
            }
        });
        toggle.append(input, el('span', 'toggle-track'));
        actions.appendChild(toggle);
        if (skill.source === 'agent') {
            actions.appendChild(confirmButton('Borrar', 'Confirmar', async () => {
                try {
                    await api(`/agent-skills/agent/${encodeURIComponent(skill.name)}`, { method: 'DELETE' });
                    await load();
                } catch (err) {
                    toast(`No se pudo borrar: ${err.message}`, true);
                }
            }));
        }
        row.append(main, actions);
        return row;
    }

    // ── Variables de entorno de skills con tools ───────────────

    async function loadToolSkills() {
        const box = $('skills-env-list');
        if (!box) return;
        try {
            const data = await api('/skills/catalog');
            const skills = Array.isArray(data.skills) ? data.skills : [];
            box.innerHTML = '';
            if (!skills.length) {
                box.appendChild(el('div', 'setting-help-text', 'No hay skills con tools instaladas.'));
                return;
            }
            skills.forEach((skill) => box.appendChild(renderToolSkill(skill)));
        } catch (err) {
            box.textContent = `No se pudo leer el catálogo de tools: ${err.message}`;
        }
    }

    function renderToolSkill(skill) {
        const card = el('div', 'mcp-server-card');
        const row = el('button', 'mcp-server-row skill-env-toggle');
        row.type = 'button';
        row.setAttribute('aria-expanded', 'false');
        const expand = el('span', 'mcp-server-expand');
        expand.innerHTML = icon('chevron-right');
        row.append(expand, el('span', 'mcp-server-name', skill.name || skill.id), el('span', 'skill-meta', 'variables'));
        const details = el('div', 'mcp-server-details');
        let loaded = false;
        row.addEventListener('click', async () => {
            const open = !card.classList.contains('expanded');
            card.classList.toggle('expanded', open);
            row.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open && !loaded) {
                loaded = true;
                await renderEnvVars(skill.id, details);
            }
        });
        card.append(row, details);
        return card;
    }

    async function renderEnvVars(skillId, container) {
        container.innerHTML = '';
        try {
            const data = await api(`/skills/${encodeURIComponent(skillId)}/env`);
            const vars = Array.isArray(data.vars) ? data.vars : [];
            if (!vars.length) {
                container.appendChild(el('div', 'setting-help-text', 'Esta herramienta no necesita variables.'));
                return;
            }
            vars.forEach((variable) => {
                const line = el('div', 'skill-env-row');
                const status = variable.configured ? ['guardada', 'is-ok'] : (variable.from_env ? ['del sistema', 'is-muted'] : ['falta', 'is-warn']);
                const label = el('label', 'skill-env-name');
                label.append(el('code', '', variable.name), el('span', `skill-chip ${status[1]}`, status[0]));
                const input = el('input', 'api-key-input');
                input.type = 'password';
                input.autocomplete = 'off';
                input.placeholder = variable.configured ? 'Guardada (escribe para reemplazar)' : 'Valor';
                input.id = `env-${skillId}-${variable.name}`;
                label.htmlFor = input.id;
                const save = el('button', 'btn-secondary btn-panel-action', 'Guardar');
                save.type = 'button';
                const clear = el('button', 'btn-secondary btn-panel-action', 'Quitar');
                clear.type = 'button';
                clear.disabled = !variable.configured;
                const put = async (value) => {
                    try {
                        await api(`/skills/${encodeURIComponent(skillId)}/env`, {
                            method: 'PUT', body: JSON.stringify({ [variable.name]: value }),
                        });
                        toast(value ? `${variable.name} guardada en el keyring.` : `${variable.name} eliminada.`);
                        await renderEnvVars(skillId, container);
                    } catch (err) {
                        toast(`No se pudo guardar ${variable.name}: ${err.message}`, true);
                    }
                };
                save.addEventListener('click', () => { if (input.value.trim()) put(input.value.trim()); });
                clear.addEventListener('click', () => put(''));
                const controls = el('div', 'skill-env-controls');
                controls.append(input, save, clear);
                line.append(label, controls);
                container.appendChild(line);
            });
        } catch (err) {
            container.appendChild(el('div', 'setting-help-text', `No se pudieron leer las variables: ${err.message}`));
        }
    }

    // ── Arranque ───────────────────────────────────────────────

    function bind() {
        const input = $('skills-install-source');
        $('btn-skills-install')?.addEventListener('click', () => install(input?.value, null));
        input?.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            install(input.value, null);
        });
        $('skills-search')?.addEventListener('input', (e) => {
            state.query = e.target.value.trim();
            renderList();
        });
        $('skills-tabs')?.addEventListener('keydown', (e) => {
            const keys = Object.keys(TABS);
            const idx = keys.indexOf(state.tab);
            const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
            if (!step) return;
            e.preventDefault();
            selectTab(keys[(idx + step + keys.length) % keys.length], true);
        });
        $('btn-skills-curator')?.addEventListener('click', async (e) => {
            const button = e.currentTarget;
            button.disabled = true;
            try {
                await api('/agent-skills/curator/run', { method: 'POST' });
                toast('Curador ejecutado: revisó las skills aprendidas.');
                await load();
            } catch (err) {
                toast(`No se pudo ejecutar el curador: ${err.message}`, true);
            } finally {
                button.disabled = false;
            }
        });
    }

    document.addEventListener('gmini:settings-page', (e) => {
        if (e.detail?.page !== 'skills' || !e.detail.open || state.loaded) return;
        state.loaded = true;
        load();
        loadToolSkills();
    });

    bind();
    window.gminiSkills = { reload: () => Promise.all([load(), loadToolSkills()]) };
})();
