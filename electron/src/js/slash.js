/**
 * G-Mini Agent — Comandos "/" en el compositor.
 *
 * Al escribir "/" al inicio del mensaje se abre la lista de comandos del
 * núcleo (GET /api/slash-commands): los incluidos y los propios de
 * data/commands. Flechas para moverse, Tab o Enter completan y Esc cierra.
 * Con el comando ya escrito, la línea de ayuda bajo el compositor muestra
 * cómo se usa. Los mismos comandos aparecen en la paleta (Ctrl+K).
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const FRESH_MS = 30000;      // los propios (archivos .md) pueden cambiar con la app abierta
    const RETRY_MS = 4000;
    const input = document.getElementById('user-input');
    const composer = document.getElementById('composer');
    const defaultHint = document.querySelector('.composer-hint');
    if (!input || !composer) return;

    const icon = (name, cls = '') => window.gminiDom.icon(name, cls);
    let commands = [];
    let fetchedAt = 0;
    let triedAt = 0;
    let loading = null;
    let results = [];
    let active = 0;
    let query = '';
    let dismissedFor = null;     // valor del compositor en el que se cerró la lista con Esc

    // ── Marcado ────────────────────────────────────────────────

    const menu = document.createElement('div');
    menu.className = 'slash-menu';
    menu.hidden = true;
    menu.innerHTML = `
        <div class="slash-menu-list" id="slash-menu-list" role="listbox" aria-label="Comandos del chat"></div>
        <div class="slash-menu-footer" aria-hidden="true">
            <span><kbd>${icon('arrow-up', 'icon-sm')}</kbd><kbd>${icon('arrow-down', 'icon-sm')}</kbd> moverse</span>
            <span><kbd>Tab</kbd> completar</span>
            <span><kbd>Esc</kbd> cerrar</span>
        </div>`;
    composer.appendChild(menu);
    const list = menu.querySelector('.slash-menu-list');

    const usage = document.createElement('div');
    usage.className = 'composer-hint slash-usage';
    usage.hidden = true;
    defaultHint?.after(usage);

    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', 'slash-menu-list');

    // ── Datos ──────────────────────────────────────────────────

    function load(force = false) {
        const now = Date.now();
        if (loading) return loading;
        if (!force && (now - fetchedAt < FRESH_MS || now - triedAt < RETRY_MS)) return Promise.resolve(commands);
        triedAt = now;
        loading = fetch(`${API}/slash-commands`)
            .then((resp) => (resp.ok ? resp.json() : Promise.reject(new Error(`HTTP ${resp.status}`))))
            .then((data) => {
                commands = (Array.isArray(data.commands) ? data.commands : [])
                    .filter((cmd) => cmd && typeof cmd.name === 'string' && cmd.name);
                fetchedAt = Date.now();
                return commands;
            })
            .catch((err) => {
                console.warn('[Comandos] No se pudo leer la lista de comandos:', err.message);
                return commands;
            })
            .finally(() => { loading = null; });
        return loading;
    }

    const norm = (text) => String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    // Los propios son plantillas: lo escrito después del comando llega como $ARGUMENTS.
    const takesArgs = (cmd) => !!cmd.custom || /[<[]/.test(cmd.usage || '');
    const argsOf = (cmd) => String(cmd.usage || '').replace(new RegExp(`^/${cmd.name}\\b`), '').trim();
    const find = (name) => commands.find((cmd) => cmd.name === name);

    /** Nombre del comando que se está escribiendo, o null si el cursor ya salió de él. */
    function typedToken() {
        const value = input.value;
        if (!value.startsWith('/') || value.startsWith('//')) return null;
        const space = value.search(/\s/);
        const end = space < 0 ? value.length : space;
        if (input.selectionStart !== input.selectionEnd || input.selectionEnd > end) return null;
        return value.slice(1, end).toLowerCase();
    }

    /** Primer token del mensaje si es un comando conocido (para la línea de uso). */
    function writtenCommand() {
        const match = /^\/([^\s/]+)/.exec(input.value);
        return match ? find(match[1].toLowerCase()) : null;
    }

    /**
     * Por niveles: los que empiezan con lo escrito; si no hay, los que lo
     * contienen; y si tampoco, los que lo mencionan en la descripción.
     * Mezclarlos ponía "/modo" (descripción: "Muestra...") delante de "/traducir" al escribir "/tra".
     */
    function rank(text) {
        const tiers = [
            (cmd) => cmd.name.toLowerCase().startsWith(text),
            (cmd) => cmd.name.toLowerCase().includes(text),
            (cmd) => text.length >= 3 && norm(cmd.description).includes(norm(text)),
        ];
        for (const test of tiers) {
            const found = commands.filter(test);
            // Incluidos primero y propios después; el nombre exacto, arriba de su grupo.
            if (found.length) return found.sort((a, b) => (a.custom - b.custom) || ((b.name === text) - (a.name === text)));
        }
        return [];
    }

    // ── Lista ──────────────────────────────────────────────────

    function open() {
        if (!menu.hidden) return;
        menu.hidden = false;
    }

    function close() {
        if (menu.hidden) return;
        menu.hidden = true;
        results = [];
        input.removeAttribute('aria-activedescendant');
    }

    function render() {
        list.replaceChildren();
        const grouped = results.some((cmd) => cmd.custom) && results.some((cmd) => !cmd.custom);
        let lastGroup = null;
        results.forEach((cmd, index) => {
            const group = cmd.custom ? 'Propios' : 'Incluidos';
            if (grouped && group !== lastGroup) {
                const heading = document.createElement('div');
                heading.className = 'slash-group';
                heading.setAttribute('role', 'presentation');
                heading.textContent = group;
                list.appendChild(heading);
                lastGroup = group;
            }
            const item = document.createElement('div');
            item.className = 'slash-item';
            item.id = `slash-opt-${index}`;
            item.dataset.index = String(index);
            item.setAttribute('role', 'option');

            const name = document.createElement('span');
            name.className = 'slash-item-name';
            const slash = document.createElement('span');
            slash.className = 'slash-item-slash';
            slash.textContent = '/';
            name.appendChild(slash);
            const at = query ? cmd.name.toLowerCase().indexOf(query) : -1;
            if (at < 0) {
                name.append(cmd.name);
            } else {
                const mark = document.createElement('mark');
                mark.textContent = cmd.name.slice(at, at + query.length);
                name.append(cmd.name.slice(0, at), mark, cmd.name.slice(at + query.length));
            }
            item.appendChild(name);

            const args = argsOf(cmd);
            if (args) {
                const argsEl = document.createElement('span');
                argsEl.className = 'slash-item-args';
                argsEl.textContent = args;
                item.appendChild(argsEl);
            }
            const desc = document.createElement('span');
            desc.className = 'slash-item-desc';
            desc.textContent = cmd.description || (cmd.custom ? 'Comando propio' : '');
            desc.title = cmd.description || '';
            item.appendChild(desc);
            if (cmd.custom) {
                const badge = document.createElement('span');
                badge.className = 'slash-item-badge';
                badge.textContent = 'Propio';
                item.appendChild(badge);
            }
            list.appendChild(item);
        });
        select(Math.min(active, results.length - 1));
    }

    function select(index, scroll = true) {
        if (!results.length) return;
        active = (index + results.length) % results.length;
        list.querySelectorAll('.slash-item').forEach((item) => {
            item.setAttribute('aria-selected', Number(item.dataset.index) === active ? 'true' : 'false');
        });
        const current = list.querySelector(`#slash-opt-${active}`);
        input.setAttribute('aria-activedescendant', current.id);
        if (scroll) current.scrollIntoView({ block: 'nearest' });
    }

    function syncUsage() {
        const cmd = menu.hidden ? writtenCommand() : null;
        usage.hidden = !cmd;
        if (defaultHint) defaultHint.hidden = !!cmd;
        if (!cmd) return;
        const code = document.createElement('code');
        code.textContent = `/${cmd.name}`;
        const parts = [code];
        const args = argsOf(cmd);
        if (args) {
            const argsEl = document.createElement('span');
            argsEl.className = 'slash-usage-args';
            argsEl.textContent = ` ${args}`;
            parts.push(argsEl);
        }
        if (cmd.description) parts.push(` · ${cmd.description}`);
        usage.replaceChildren(...parts);
    }

    /** Relee el compositor: abre, filtra o cierra la lista y actualiza la línea de uso. */
    function refresh() {
        const token = typedToken();
        if (token === null || input.value === dismissedFor) {
            close();
            syncUsage();
            return;
        }
        const stale = Date.now() - fetchedAt >= FRESH_MS;
        if (stale) {
            load().then(() => {
                // Solo si la lista sigue haciendo falta (pudo borrarse el "/").
                if (typedToken() !== null && fetchedAt) refreshNow();
            });
        }
        if (!commands.length) {
            close();
            syncUsage();
            return;
        }
        refreshNow();
    }

    function refreshNow() {
        const token = typedToken();
        if (token === null || input.value === dismissedFor) {
            close();
            syncUsage();
            return;
        }
        // Mismo texto (cursor movido o lista recargada): se conserva la opción marcada.
        const previous = token === query && !menu.hidden ? results[active]?.name : null;
        query = token;
        results = rank(token);
        if (!results.length) {
            close();
            syncUsage();
            return;
        }
        const keep = previous ? results.findIndex((cmd) => cmd.name === previous) : -1;
        active = Math.max(keep, 0);
        open();
        render();
        syncUsage();
    }

    // ── Completar ──────────────────────────────────────────────

    /** Escribe "/nombre" al inicio y deja el resto del mensaje como argumentos. */
    function insert(cmd, rest) {
        const head = `/${cmd.name}`;
        const tail = rest.replace(/^[ \t]+/, '');
        const spaced = takesArgs(cmd) || tail;
        const next = spaced ? `${head} ${tail}` : head;
        dismissedFor = next;     // un comando sin argumentos ya completo no reabre la lista
        close();
        if (window.gminiComposer) window.gminiComposer.setText(next);
        else input.value = next;
        const caret = head.length + (spaced ? 1 : 0);
        input.setSelectionRange(caret, caret);
        syncUsage();
    }

    function complete(cmd) {
        const value = input.value;
        const space = value.search(/\s/);
        insert(cmd, space < 0 ? '' : value.slice(space));
    }

    // Antes que el Enter-para-enviar de app.js: captura en el compositor.
    composer.addEventListener('keydown', (e) => {
        if (e.target !== input || menu.hidden || e.isComposing || !results.length) return;
        const cmd = results[active];
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            if (e.altKey || e.ctrlKey || e.metaKey) return;
            select(active + (e.key === 'ArrowDown' ? 1 : -1));
        } else if (e.key === 'Tab' && !e.shiftKey) {
            complete(cmd);
        } else if (e.key === 'Enter' && !e.shiftKey) {
            // El comando marcado ya está escrito entero: Enter envía como siempre.
            if (query === cmd.name) {
                close();
                return;
            }
            complete(cmd);
        } else if (e.key === 'Escape') {
            dismissedFor = input.value;
            close();
            syncUsage();
        } else {
            return;
        }
        e.preventDefault();
        e.stopPropagation();
    }, true);

    input.addEventListener('input', () => {
        if (input.value !== dismissedFor) dismissedFor = null;
        refresh();
    });
    // El cursor puede salir o entrar del comando sin escribir nada.
    input.addEventListener('keyup', (e) => {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) refresh();
    });
    input.addEventListener('click', refresh);
    input.addEventListener('blur', () => {
        close();
        syncUsage();
    });

    // Clic en una opción: el compositor conserva el foco (app.js ya evita el blur).
    menu.addEventListener('mousedown', (e) => e.preventDefault());
    list.addEventListener('mousemove', (e) => {
        const item = e.target.closest('.slash-item');
        if (item && Number(item.dataset.index) !== active) select(Number(item.dataset.index), false);
    });
    list.addEventListener('click', (e) => {
        const item = e.target.closest('.slash-item');
        if (item) complete(results[Number(item.dataset.index)]);
    });

    // ── Paleta (Ctrl+K) ────────────────────────────────────────

    if (typeof commandPalette !== 'undefined') {
        commandPalette.registerSource(() => {
            load();
            return commands.map((cmd) => ({
                id: `slash.${cmd.name}`,
                title: `/${cmd.name}: ${cmd.description || (cmd.custom ? 'comando propio' : '')}`,
                group: 'Comandos del chat',
                icon: cmd.custom ? 'file-text' : 'slash',
                keywords: ['comando', 'barra', cmd.custom ? 'propio' : 'incluido'],
                keepsFocus: true,
                // Lo que ya estaba escrito pasa a ser el argumento del comando.
                run: () => {
                    const draft = /^\/\S*/.test(input.value) ? input.value.replace(/^\/\S*/, '') : ` ${input.value}`;
                    insert(cmd, input.value ? draft : '');
                    input.focus();
                },
            }));
        });
    }

    // Lista lista antes del primer "/" (y al reconectar, por si cambió el núcleo).
    if (typeof ws !== 'undefined') {
        ws.on('connected', () => load(true));
        if (ws.connected) load(true);
    }

    window.gminiSlash = { refresh, reload: () => load(true) };
})();
