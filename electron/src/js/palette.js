/**
 * G-Mini Agent — Paleta de comandos (Ctrl+K) y ayuda de atajos (Ctrl+/).
 *
 * Un registro único de acciones (título, grupo, icono, palabras clave, atajo
 * y run). Las fuentes dinámicas (conversaciones, modos, proveedores, modelos)
 * se consultan al abrir para estar siempre al día. La ayuda de atajos se
 * genera del mismo registro, así no se desincroniza.
 */

const PALETTE_GROUP_ORDER = [
    'General', 'Conversaciones', 'Agente', 'Comandos del chat', 'Vista', 'Configuración',
    'Apariencia', 'Modos', 'Proveedor', 'Modelo',
];

function paletteNormalize(text) {
    return String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

class CommandPalette {
    constructor() {
        this.commands = new Map();
        this.sources = [];
        this.backdrop = null;
        this.mode = 'commands';
        this.results = [];
        this.selected = 0;
        this._returnFocus = null;
        this._onKeydown = (e) => this._handleKeydown(e);
    }

    register(command) {
        this.commands.set(command.id, command);
        return this;
    }

    /** fn() -> comandos generados al abrir (conversaciones, modelos...). */
    registerSource(fn) {
        this.sources.push(fn);
        return this;
    }

    isOpen() {
        return !!this.backdrop;
    }

    toggle() {
        if (this.isOpen() && this.mode === 'commands') this.close();
        else this.open('commands');
    }

    open(mode = 'commands', initialQuery = '') {
        if (this.isOpen()) this.close({ restoreFocus: false });
        this.mode = mode;
        if (!this._returnFocus) this._returnFocus = document.activeElement;
        this.backdrop = document.createElement('div');
        this.backdrop.className = 'palette-backdrop';
        this.backdrop.addEventListener('mousedown', (e) => {
            if (e.target === this.backdrop) this.close();
        });
        if (mode === 'help') this._buildHelp();
        else this._buildCommands(initialQuery);
        document.body.appendChild(this.backdrop);
        document.addEventListener('keydown', this._onKeydown, true);
        const focusTarget = this.backdrop.querySelector('.palette-input') || this.backdrop.querySelector('.btn-icon');
        focusTarget?.focus();
    }

    close({ restoreFocus = true } = {}) {
        if (!this.backdrop) return;
        this.backdrop.remove();
        this.backdrop = null;
        document.removeEventListener('keydown', this._onKeydown, true);
        const back = this._returnFocus;
        this._returnFocus = null;
        if (restoreFocus && back && document.contains(back) && typeof back.focus === 'function') back.focus();
    }

    // ── Modo comandos ─────────────────────────────────────────

    _buildCommands(initialQuery) {
        const icon = window.gminiDom.icon;
        this.backdrop.innerHTML = `
            <div class="palette" role="dialog" aria-modal="true" aria-label="Paleta de comandos">
                <div class="palette-search">
                    ${icon('search')}
                    <input class="palette-input" type="text" role="combobox" aria-expanded="true"
                        aria-controls="palette-list" aria-autocomplete="list" spellcheck="false" autocomplete="off"
                        placeholder="Busca una acción, conversación, modelo o tema">
                    <kbd>Esc</kbd>
                </div>
                <div class="palette-list" id="palette-list" role="listbox" aria-label="Resultados"></div>
                <div class="palette-footer">
                    <span><kbd>${icon('arrow-up', 'icon-sm')}</kbd><kbd>${icon('arrow-down', 'icon-sm')}</kbd> moverse</span>
                    <span><kbd>${icon('corner-down-left', 'icon-sm')}</kbd> ejecutar</span>
                    <span><kbd>Ctrl</kbd><kbd>/</kbd> atajos</span>
                </div>
            </div>`;
        this.input = this.backdrop.querySelector('.palette-input');
        this.list = this.backdrop.querySelector('.palette-list');
        this.input.value = initialQuery;
        this.input.addEventListener('input', () => this._refresh());
        this.list.addEventListener('mousemove', (e) => {
            const item = e.target.closest('.palette-item');
            if (item) this._select(Number(item.dataset.index), false);
        });
        this.list.addEventListener('click', (e) => {
            const item = e.target.closest('.palette-item');
            if (item) this._run(Number(item.dataset.index));
        });
        this._refresh();
    }

    _allCommands() {
        const list = Array.from(this.commands.values());
        for (const source of this.sources) {
            try {
                list.push(...(source() || []));
            } catch (err) {
                console.warn('[Paleta] Fuente de comandos con error:', err);
            }
        }
        return list.filter((cmd) => {
            try { return !cmd.when || cmd.when(); } catch (e) { return false; }
        });
    }

    _score(cmd, query) {
        if (!query) return 1;
        const title = paletteNormalize(cmd.title);
        const haystack = paletteNormalize([cmd.title, cmd.group, ...(cmd.keywords || [])].join(' '));
        if (title.startsWith(query)) return 100 - title.length / 100;
        const words = query.split(/\s+/).filter(Boolean);
        if (words.every((w) => haystack.includes(w))) {
            const wordStart = title.split(/[\s:·/-]+/).some((part) => part.startsWith(words[0]));
            return (wordStart ? 60 : 40) - title.length / 100;
        }
        // Iniciales de las palabras ("nc" -> "Nueva conversación"). Una
        // subsecuencia libre traía demasiado ruido ("tema" casaba con "Exportar...").
        const initials = title.split(/[\s:·/-]+/).filter(Boolean).map((w) => w[0]).join('');
        return query.length >= 2 && initials.includes(query.replace(/\s+/g, '')) ? 10 - title.length / 100 : 0;
    }

    _refresh() {
        const query = paletteNormalize(this.input.value.trim());
        let scored = this._allCommands()
            .map((cmd) => ({ cmd, score: this._score(cmd, query) }))
            .filter((r) => r.score > 0);

        if (query) {
            scored.sort((a, b) => b.score - a.score);
        } else {
            // Sin búsqueda: por grupos y sin listas largas (modelos y modos aparecen al buscar).
            const caps = { Conversaciones: 6, 'Comandos del chat': 0, Modelo: 0, Proveedor: 0, Modos: 0 };
            const order = (group) => {
                const idx = PALETTE_GROUP_ORDER.indexOf(group);
                return idx < 0 ? PALETTE_GROUP_ORDER.length : idx;
            };
            scored.sort((a, b) => order(a.cmd.group) - order(b.cmd.group));
            const kept = {};
            scored = scored.filter(({ cmd }) => {
                kept[cmd.group] = (kept[cmd.group] || 0) + 1;
                return caps[cmd.group] === undefined || kept[cmd.group] <= caps[cmd.group];
            });
        }
        this.results = scored.slice(0, 60).map((r) => r.cmd);
        this._renderResults(query);
    }

    _renderResults(query) {
        const icon = window.gminiDom.icon;
        this.list.innerHTML = '';
        if (!this.results.length) {
            const empty = document.createElement('div');
            empty.className = 'palette-empty';
            empty.textContent = 'Sin resultados. Prueba con otra palabra.';
            this.list.appendChild(empty);
            this.input.removeAttribute('aria-activedescendant');
            return;
        }
        let lastGroup = null;
        this.results.forEach((cmd, index) => {
            const group = query ? '' : cmd.group;
            if (group && group !== lastGroup) {
                const heading = document.createElement('div');
                heading.className = 'palette-group';
                heading.setAttribute('role', 'presentation');
                heading.textContent = group;
                this.list.appendChild(heading);
                lastGroup = group;
            }
            const item = document.createElement('div');
            item.className = 'palette-item';
            item.id = `palette-opt-${index}`;
            item.setAttribute('role', 'option');
            item.dataset.index = String(index);
            const iconWrap = document.createElement('span');
            iconWrap.className = 'palette-item-icon';
            iconWrap.innerHTML = icon(cmd.icon || 'command');
            const label = document.createElement('span');
            label.className = 'palette-item-label';
            this._appendHighlighted(label, cmd.title, query);
            item.append(iconWrap, label);
            const meta = document.createElement('span');
            meta.className = 'palette-item-meta';
            if (cmd.checked) {
                meta.innerHTML = icon('check', 'palette-item-check');
            } else if (cmd.shortcut) {
                cmd.shortcut.split('+').forEach((key) => {
                    const kbd = document.createElement('kbd');
                    kbd.textContent = key;
                    meta.appendChild(kbd);
                });
            } else if (query && cmd.group) {
                meta.textContent = cmd.group;
            }
            if (meta.childNodes.length) item.appendChild(meta);
            this.list.appendChild(item);
        });
        this._select(0, true);
    }

    /** Resalta la coincidencia sin innerHTML: el título puede venir del modelo o del historial. */
    _appendHighlighted(el, title, query) {
        const text = String(title || '');
        const idx = query ? paletteNormalize(text).indexOf(query) : -1;
        if (idx < 0) {
            el.textContent = text;
            return;
        }
        const mark = document.createElement('mark');
        mark.textContent = text.slice(idx, idx + query.length);
        el.append(text.slice(0, idx), mark, text.slice(idx + query.length));
    }

    _select(index, scroll) {
        const items = this.list.querySelectorAll('.palette-item');
        if (!items.length) return;
        this.selected = Math.max(0, Math.min(index, items.length - 1));
        items.forEach((item, i) => item.setAttribute('aria-selected', i === this.selected ? 'true' : 'false'));
        const active = items[this.selected];
        this.input.setAttribute('aria-activedescendant', active.id);
        if (scroll) active.scrollIntoView({ block: 'nearest' });
    }

    _run(index) {
        const cmd = this.results[index];
        if (!cmd) return;
        this.close({ restoreFocus: !cmd.keepsFocus });
        Promise.resolve()
            .then(() => cmd.run())
            .catch((err) => {
                console.error('[Paleta] Error al ejecutar', cmd.id, err);
                chatManager?._toast?.(`No se pudo ejecutar "${cmd.title}"`, true);
            });
    }

    // ── Modo ayuda de atajos ──────────────────────────────────

    _buildHelp() {
        const icon = window.gminiDom.icon;
        this.backdrop.innerHTML = `
            <div class="palette" role="dialog" aria-modal="true" aria-labelledby="shortcut-help-title">
                <div class="palette-search">
                    ${icon('keyboard')}
                    <span class="palette-title" id="shortcut-help-title">Atajos de teclado</span>
                    <button class="btn-icon" type="button" aria-label="Cerrar">${icon('x')}</button>
                </div>
                <div class="shortcut-list"></div>
            </div>`;
        this.backdrop.querySelector('.btn-icon').addEventListener('click', () => this.close());
        const list = this.backdrop.querySelector('.shortcut-list');
        const sections = [
            ['En la ventana', Array.from(this.commands.values()).filter((c) => c.shortcut).map((c) => [c.title, c.shortcut])],
            ['Compositor', [['Enviar mensaje', 'Enter'], ['Nueva línea', 'Shift+Enter'], ['Cerrar panel o diálogo', 'Esc']]],
        ];
        const renderSections = (extra) => {
            list.innerHTML = '';
            [...sections, ...(extra ? [extra] : [])].forEach(([title, rows]) => {
                if (!rows.length) return;
                const head = document.createElement('div');
                head.className = 'shortcut-section';
                head.textContent = title;
                list.appendChild(head);
                rows.forEach(([label, combo]) => {
                    const row = document.createElement('div');
                    row.className = 'shortcut-item';
                    const name = document.createElement('span');
                    name.textContent = label;
                    const keys = document.createElement('span');
                    keys.className = 'shortcut-keys';
                    String(combo).split('+').forEach((key) => {
                        const kbd = document.createElement('kbd');
                        kbd.textContent = key;
                        keys.appendChild(kbd);
                    });
                    row.append(name, keys);
                    list.appendChild(row);
                });
            });
        };
        renderSections(null);
        // Los atajos globales viven en el proceso principal (configurables en General).
        window.gmini?.getShortcuts?.().then((shortcuts) => {
            if (!this.isOpen() || this.mode !== 'help' || !shortcuts) return;
            const labels = { toggle_window: 'Mostrar u ocultar la ventana', toggle_overlay: 'Modo overlay', quit: 'Salir' };
            const rows = Object.entries(shortcuts)
                .filter(([key, combo]) => combo && labels[key])
                .map(([key, combo]) => [labels[key], combo]);
            renderSections(['Globales (funcionan sin foco)', rows]);
        }).catch(() => {});
    }

    // ── Teclado ───────────────────────────────────────────────

    _handleKeydown(e) {
        if (!this.isOpen()) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            this.close();
            return;
        }
        const dialog = this.backdrop.querySelector('.palette');
        if (e.key === 'Tab') {
            window.gminiDom.trapFocus(dialog, e);
            return;
        }
        if (this.mode !== 'commands') return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            this._select(this.selected + 1, true);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            this._select(this.selected - 1, true);
        } else if (e.key === 'PageDown') {
            e.preventDefault();
            this._select(this.selected + 8, true);
        } else if (e.key === 'PageUp') {
            e.preventDefault();
            this._select(this.selected - 8, true);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            this._run(this.selected);
        }
    }
}

const commandPalette = new CommandPalette();
window.gminiPalette = commandPalette;

// ── Acciones registradas ──────────────────────────────────────

(function registerDefaultCommands(palette) {
    const openPanelTab = async (tab) => {
        await window.codeManager?.togglePanel?.(true);
        await window.codeManager?.setActiveTab?.(tab);
    };
    const settingsPages = [
        ['general', 'General', 'sliders-horizontal', ['tema', 'ventana', 'atajos', 'monitor', 'autonomia']],
        ['personality', 'Personalidad', 'bot', ['nombre', 'identidad', 'acento']],
        ['model', 'Modelo IA', 'cpu', ['proveedor', 'api key', 'claves', 'embeddings', 'temperatura']],
        ['voice', 'Voz y personaje', 'mic', ['tts', 'avatar', 'skin', 'voz']],
        ['prompts', 'Prompts', 'file-text', ['plantillas', 'modo personalizado']],
        ['security', 'Seguridad y permisos', 'shield', ['presupuesto', 'gastos', 'bloqueadas', 'token']],
        ['integrations', 'Integraciones', 'waypoints', ['mcp', 'telegram', 'whatsapp', 'discord', 'skills']],
        ['scheduler', 'Scheduler', 'clock', ['tareas programadas', 'cron']],
        ['crews', 'Equipos', 'users', ['crews', 'multi-agente', 'roles']],
    ];
    const panelTabs = [
        ['workspace', 'Workspace y código', 'git-branch', ['archivos', 'proyecto', 'git']],
        ['scheduler', 'Jobs programados', 'calendar', ['scheduler', 'cron', 'costos']],
        ['canvas', 'Canvas', 'layers', ['dashboards']],
        ['security', 'Seguridad y auditoría', 'shield', ['rbac', 'auditoria', 'sandbox']],
        ['analytics', 'Analytics', 'chart-column', ['tokens', 'errores', 'objetivos']],
    ];
    const themeNames = {
        system: 'Sistema', dark: 'Grafito', ocean: 'Océano', midnight: 'Medianoche',
        light: 'Claro', paper: 'Papel', contrast: 'Alto contraste',
    };
    const themeIcons = { system: 'monitor', light: 'sun', paper: 'sun', contrast: 'contrast' };
    const accentNames = { blue: 'Azul', violet: 'Violeta', green: 'Verde', amber: 'Ámbar', rose: 'Rosa', cyan: 'Cian' };
    const pickSelect = (id, value) => {
        const select = document.getElementById(id);
        if (!select) return;
        select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
    };
    const busy = () => ['thinking', 'responding', 'executing', 'paused'].includes(
        document.getElementById('status-indicator')?.className.split(' ').find((c) => c !== 'status-dot') || ''
    );

    palette
        .register({ id: 'chat.new', title: 'Nueva conversación', group: 'General', icon: 'message-square-plus', shortcut: 'Ctrl+N', keywords: ['nuevo', 'chat', 'sesion'], run: () => historyManager.createNewChat() })
        .register({ id: 'chat.export', title: 'Exportar conversación a Markdown', group: 'General', icon: 'file-down', shortcut: 'Ctrl+Shift+E', keywords: ['guardar', 'descargar', 'md'], run: () => historyManager.exportCurrentConversation() })
        .register({ id: 'chat.search', title: 'Buscar conversaciones', group: 'General', icon: 'search', keywords: ['historial', 'filtrar'], keepsFocus: true, run: () => historyManager.focusSearch() })
        .register({ id: 'chat.focus', title: 'Escribir un mensaje', group: 'General', icon: 'pencil', keywords: ['compositor', 'enfocar'], keepsFocus: true, run: () => window.gminiComposer?.focus() })
        .register({ id: 'help.shortcuts', title: 'Atajos de teclado', group: 'General', icon: 'keyboard', shortcut: 'Ctrl+/', keywords: ['ayuda', 'teclas'], keepsFocus: true, run: () => palette.open('help') })
        .register({ id: 'view.sidebar', title: 'Mostrar u ocultar el historial', group: 'Vista', icon: 'panel-left', shortcut: 'Ctrl+H', keywords: ['sidebar', 'barra lateral'], run: () => historyManager.toggleSidebar() })
        .register({ id: 'view.settings', title: 'Abrir configuración', group: 'Configuración', icon: 'settings', shortcut: 'Ctrl+,', keywords: ['ajustes', 'preferencias'], keepsFocus: true, run: () => settingsManager.show() })
        .register({ id: 'view.overlay', title: 'Activar o desactivar el modo overlay', group: 'Vista', icon: 'layers', keywords: ['flotante', 'texto'], run: () => {
            const cb = document.getElementById('cb-overlay');
            if (!cb) return;
            cb.checked = !cb.checked;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
        } })
        .register({ id: 'view.avatar', title: 'Cambiar a avatar flotante', group: 'Vista', icon: 'bot', keywords: ['personaje', 'skin', 'escritorio'], run: () => pickSelect('select-display-mode', 'skin') })
        .register({ id: 'agent.pause', title: 'Pausar al agente', group: 'Agente', icon: 'pause', keywords: ['detener momentaneamente'], when: busy, run: () => document.getElementById('btn-agent-pause')?.click() })
        .register({ id: 'agent.resume', title: 'Reanudar al agente', group: 'Agente', icon: 'play', keywords: ['continuar'], when: busy, run: () => document.getElementById('btn-agent-start')?.click() })
        .register({ id: 'agent.stop', title: 'Detener al agente', group: 'Agente', icon: 'square', keywords: ['cancelar', 'parar'], when: busy, run: () => document.getElementById('btn-agent-stop')?.click() });

    settingsPages.forEach(([page, label, icon, keywords]) => {
        palette.register({
            id: `settings.${page}`, title: `Configuración: ${label}`, group: 'Configuración', icon, keywords,
            keepsFocus: true, run: () => settingsManager.openPage(page),
        });
    });
    panelTabs.forEach(([tab, label, icon, keywords]) => {
        palette.register({ id: `panel.${tab}`, title: `Panel: ${label}`, group: 'Vista', icon, keywords: ['panel', ...keywords], run: () => openPanelTab(tab) });
    });

    // Apariencia: generadas para reflejar la selección actual.
    palette.registerSource(() => {
        const prefs = window.gminiTheme?.get() || {};
        const set = (partial) => settingsManager._setAppearance(partial);
        return [
            ...Object.entries(themeNames).map(([key, label]) => ({
                id: `theme.${key}`, title: `Tema: ${label}`, group: 'Apariencia', icon: themeIcons[key] || 'moon',
                keywords: ['tema', 'color', 'oscuro', 'claro'], checked: prefs.theme === key, run: () => set({ theme: key }),
            })),
            ...Object.entries(accentNames).map(([key, label]) => ({
                id: `accent.${key}`, title: `Acento: ${label}`, group: 'Apariencia', icon: 'palette',
                keywords: ['acento', 'color'], checked: prefs.accent === key, run: () => set({ accent: key }),
            })),
            {
                id: 'density.toggle', title: prefs.density === 'compact' ? 'Densidad cómoda' : 'Densidad compacta',
                group: 'Apariencia', icon: 'sliders-horizontal', keywords: ['densidad', 'espaciado'],
                run: () => set({ density: prefs.density === 'compact' ? 'comfortable' : 'compact' }),
            },
        ];
    });

    // Conversaciones guardadas (más recientes primero).
    palette.registerSource(() => (historyManager.sessions || []).map((session) => ({
        id: `session.${session.session_id}`,
        title: session.title || historyManager._generateTitle(session),
        group: 'Conversaciones', icon: 'message-square',
        keywords: [session.mode || '', 'conversacion', 'historial'],
        checked: session.session_id === historyManager.currentSessionId,
        run: () => historyManager.loadSession(session.session_id),
    })));

    // Modos, proveedores y modelos: se leen de los selects de Configuración,
    // que ya saben aplicar y persistir el cambio.
    const fromSelect = (id, group, icon, prefix) => () => {
        const select = document.getElementById(id);
        if (!select) return [];
        return Array.from(select.options)
            .filter((opt) => opt.value && opt.value !== 'none')
            .map((opt) => ({
                id: `${id}.${opt.value}`, title: `${prefix}${opt.textContent.trim()}`, group, icon,
                keywords: [opt.value], checked: select.value === opt.value,
                run: () => pickSelect(id, opt.value),
            }));
    };
    palette.registerSource(fromSelect('select-mode', 'Modos', 'zap', 'Modo: '));
    palette.registerSource(fromSelect('select-provider', 'Proveedor', 'database', 'Proveedor: '));
    palette.registerSource(fromSelect('select-model', 'Modelo', 'cpu', 'Modelo: '));
})(commandPalette);

// ── Atajos globales de la ventana ─────────────────────────────

document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const key = e.key.toLowerCase();
    if (key === 'k' && !e.shiftKey) {
        e.preventDefault();
        commandPalette.toggle();
    } else if (key === '/' || e.code === 'Slash' || e.code === 'NumpadDivide') {
        e.preventDefault();
        if (commandPalette.isOpen() && commandPalette.mode === 'help') commandPalette.close();
        else commandPalette.open('help');
    } else if (key === ',' && !e.shiftKey) {
        e.preventDefault();
        settingsManager.toggle();
    } else if (key === 'e' && e.shiftKey) {
        e.preventDefault();
        historyManager.exportCurrentConversation();
    }
});

document.getElementById('btn-command-palette')?.addEventListener('click', () => commandPalette.open('commands'));
