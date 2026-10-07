/**
 * G-Mini Agent — Configuración > Memoria.
 *
 * Lo que el agente recuerda entre conversaciones (memoria de largo plazo):
 * filtrar por categoría, buscar, ajustar importancia, borrar, exportar,
 * consolidar y olvidar todo con confirmación escrita (sin confirm() del
 * navegador). Se carga al abrir la página por primera vez.
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const PAGE_SIZE = 30;
    const CONFIRM_WORD = 'OLVIDAR';
    const CATEGORIES = {
        fact: 'Datos',
        preference: 'Preferencias',
        task: 'Tareas',
        learning: 'Aprendizajes',
        entity: 'Entidades',
        relationship: 'Relaciones',
        skill_memory: 'Habilidades',
    };

    const state = { loaded: false, loading: false, category: '', query: '', offset: 0, items: [], hasMore: false };
    const $ = (id) => document.getElementById(id);
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

    function formatDate(value) {
        if (!value) return '';
        const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleDateString('es', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    // ── Estadísticas y preferencias ────────────────────────────

    async function loadStats() {
        const box = $('memory-stats');
        if (!box) return;
        try {
            const stats = await api('/memory/ltm/stats');
            const learning = $('cb-learning-enabled');
            if (learning && typeof stats.learning_enabled === 'boolean') learning.checked = stats.learning_enabled;
            box.innerHTML = '';
            [[stats.active ?? 0, 'recuerdos activos'], [stats.merged ?? 0, 'consolidados']].forEach(([value, label]) => {
                const stat = el('div', 'memory-stat');
                stat.append(el('span', 'memory-stat-value', String(value)), el('span', 'memory-stat-label', label));
                box.appendChild(stat);
            });
            if (stats.embedding_model) {
                const model = el('div', 'memory-stat is-wide');
                model.append(el('span', 'memory-stat-value is-mono', String(stats.embedding_model)), el('span', 'memory-stat-label', 'modelo de embeddings'));
                box.appendChild(model);
            }
        } catch (err) {
            box.textContent = `No se pudieron leer las estadísticas: ${err.message}`;
        }
    }

    async function loadProfilePreference() {
        const select = $('select-profile-build');
        if (!select) return;
        try {
            const data = await api('/config/onboarding');
            const value = data?.data?.onboarding?.profile_build;
            if (value === 'ask' || value === 'off') select.value = value;
        } catch (e) { /* sección aún no creada: queda el valor por defecto */ }
    }

    async function saveConfig(section, key, value, okMessage) {
        const ok = await window.settingsManager?._saveConfigValue?.(section, key, value);
        toast(ok ? okMessage : 'No se pudo guardar el ajuste de memoria.', !ok);
        return ok;
    }

    // ── Lista ──────────────────────────────────────────────────

    function renderFilters() {
        const box = $('memory-categories');
        if (!box) return;
        box.innerHTML = '';
        [['', 'Todas'], ...Object.entries(CATEGORIES)].forEach(([key, label]) => {
            const chip = el('button', 'memory-filter', label);
            chip.type = 'button';
            chip.setAttribute('aria-pressed', state.category === key ? 'true' : 'false');
            if (key) chip.dataset.category = key;
            chip.addEventListener('click', () => {
                state.category = key;
                renderFilters();
                loadList();
            });
            box.appendChild(chip);
        });
    }

    async function loadList({ append = false } = {}) {
        const list = $('memory-list');
        if (!list || state.loading) return;
        state.loading = true;
        if (!append) state.offset = 0;
        list.setAttribute('aria-busy', 'true');
        try {
            let rows;
            if (state.query) {
                const body = { query: state.query, top_k: PAGE_SIZE };
                if (state.category) body.category = state.category;
                const data = await api('/memory/ltm/search', { method: 'POST', body: JSON.stringify(body) });
                rows = Array.isArray(data.results) ? data.results : [];
                state.hasMore = false;
            } else {
                const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.offset) });
                if (state.category) params.set('category', state.category);
                const data = await api(`/memory/ltm?${params}`);
                rows = Array.isArray(data.memories) ? data.memories : [];
                state.hasMore = rows.length === PAGE_SIZE;
                state.offset += rows.length;
            }
            state.items = append ? state.items.concat(rows) : rows;
            renderList();
        } catch (err) {
            list.innerHTML = '';
            list.appendChild(el('div', 'memory-empty', `No se pudo cargar la memoria: ${err.message}`));
        } finally {
            state.loading = false;
            list.removeAttribute('aria-busy');
        }
    }

    function renderList() {
        const list = $('memory-list');
        list.innerHTML = '';
        if (!state.items.length) {
            const empty = el('div', 'memory-empty');
            empty.innerHTML = window.gminiDom.icon('brain');
            empty.appendChild(el('span', '', state.query
                ? `Nada en la memoria coincide con "${state.query}".`
                : 'Todavía no hay recuerdos en esta categoría.'));
            list.appendChild(empty);
        }
        state.items.forEach((memory) => list.appendChild(renderItem(memory)));
        const more = $('btn-memory-more');
        if (more) more.hidden = !state.hasMore;
    }

    function renderItem(memory) {
        const id = String(memory.memory_id || memory.id || '');
        const category = String(memory.category || 'fact');
        const item = el('article', 'memory-item');
        item.dataset.id = id;

        const head = el('div', 'memory-item-head');
        const chip = el('span', 'memory-chip', CATEGORIES[category] || category);
        chip.dataset.category = category;
        const meta = [formatDate(memory.created_at)];
        if (typeof memory.score === 'number') meta.push(`relevancia ${Math.round(memory.score * 100)}%`);
        head.append(chip, el('span', 'memory-item-meta', meta.filter(Boolean).join(' · ')));

        const content = el('p', 'memory-item-content', String(memory.content || ''));

        const foot = el('div', 'memory-item-foot');
        foot.append(renderImportance(id, Number(memory.importance)), renderDelete(id, item));
        item.append(head, content, foot);
        return item;
    }

    /** Importancia 0..1 como 5 niveles: radiogroup con flechas. */
    function renderImportance(id, importance) {
        const group = el('div', 'memory-importance');
        group.setAttribute('role', 'radiogroup');
        group.setAttribute('aria-label', 'Importancia');
        group.appendChild(el('span', 'memory-importance-label', 'Importancia'));
        const level = Math.max(1, Math.min(5, Math.round((Number.isFinite(importance) ? importance : 0.5) * 5)));
        const paint = (current) => {
            group.querySelectorAll('button').forEach((dot, i) => {
                dot.classList.toggle('is-filled', i < current);
                dot.setAttribute('aria-checked', i + 1 === current ? 'true' : 'false');
                dot.tabIndex = i + 1 === current ? 0 : -1;
            });
        };
        for (let i = 1; i <= 5; i += 1) {
            const dot = el('button');
            dot.type = 'button';
            dot.setAttribute('role', 'radio');
            dot.setAttribute('aria-label', `Importancia ${i} de 5`);
            dot.title = `Importancia ${i} de 5`;
            dot.addEventListener('click', () => setImportance(id, i, paint));
            dot.addEventListener('keydown', (e) => {
                const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
                if (!step) return;
                e.preventDefault();
                const target = Math.max(1, Math.min(5, i + step));
                group.querySelectorAll('button')[target - 1].focus();
                setImportance(id, target, paint);
            });
            group.appendChild(dot);
        }
        paint(level);
        return group;
    }

    async function setImportance(id, level, paint) {
        paint(level);
        try {
            await api(`/memory/ltm/${encodeURIComponent(id)}/importance`, {
                method: 'PUT',
                body: JSON.stringify({ importance: level / 5 }),
            });
            const memory = state.items.find((m) => String(m.memory_id || m.id) === id);
            if (memory) memory.importance = level / 5;
        } catch (err) {
            toast(`No se pudo cambiar la importancia: ${err.message}`, true);
        }
    }

    /** Borrar en dos pasos: el primer clic pide confirmación en el propio botón. */
    function renderDelete(id, item) {
        const button = el('button', 'btn-icon memory-delete');
        button.type = 'button';
        const idle = () => {
            button.classList.remove('is-confirming');
            button.innerHTML = window.gminiDom.icon('trash-2');
            button.setAttribute('aria-label', 'Borrar este recuerdo');
            button.title = 'Borrar este recuerdo';
        };
        idle();
        button.addEventListener('click', async () => {
            if (!button.classList.contains('is-confirming')) {
                button.classList.add('is-confirming');
                button.textContent = 'Confirmar borrado';
                button.setAttribute('aria-label', 'Confirmar el borrado de este recuerdo');
                clearTimeout(button._timer);
                button._timer = setTimeout(idle, 4000);
                return;
            }
            clearTimeout(button._timer);
            button.disabled = true;
            try {
                await api(`/memory/ltm/${encodeURIComponent(id)}`, { method: 'DELETE' });
                state.items = state.items.filter((m) => String(m.memory_id || m.id) !== id);
                item.remove();
                if (!state.items.length) renderList();
                loadStats();
            } catch (err) {
                button.disabled = false;
                idle();
                toast(`No se pudo borrar: ${err.message}`, true);
            }
        });
        return button;
    }

    // ── Acciones globales ──────────────────────────────────────

    async function exportMemory() {
        try {
            const data = await api('/memory/ltm/export');
            const content = JSON.stringify(data.memories || [], null, 2);
            if (!window.gmini?.saveTextAs) throw new Error('exportar no está disponible');
            const res = await window.gmini.saveTextAs({ suggestedName: 'memoria-g-mini.json', content, kind: 'json' });
            if (res?.ok) toast(`Memoria exportada: ${res.path}`);
            else if (!res?.canceled) toast(`No se pudo exportar${res?.error ? `: ${res.error}` : ''}`, true);
        } catch (err) {
            toast(`No se pudo exportar la memoria: ${err.message}`, true);
        }
    }

    async function consolidate(button) {
        button.disabled = true;
        try {
            await api('/memory/ltm/consolidate', { method: 'POST' });
            toast('Memoria consolidada: se unieron los recuerdos duplicados.');
            await Promise.all([loadStats(), loadList()]);
        } catch (err) {
            toast(`No se pudo consolidar: ${err.message}`, true);
        } finally {
            button.disabled = false;
        }
    }

    async function forgetAll() {
        const input = $('input-memory-forget-confirm');
        const button = $('btn-memory-forget-all');
        const meta = $('memory-forget-meta');
        if (String(input?.value || '').trim().toUpperCase() !== CONFIRM_WORD) return;
        button.disabled = true;
        try {
            const data = await api('/memory/ltm?confirm=true', { method: 'DELETE' });
            input.value = '';
            if (meta) meta.textContent = `Se borraron ${Number(data.deleted) || 0} recuerdos.`;
            await Promise.all([loadStats(), loadList()]);
        } catch (err) {
            if (meta) meta.textContent = `No se pudo borrar la memoria: ${err.message}`;
            button.disabled = false;
        }
    }

    // ── Arranque ───────────────────────────────────────────────

    function bind() {
        let searchTimer = null;
        const search = $('memory-search');
        search?.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                state.query = search.value.trim();
                loadList();
            }, 300);
        });
        search?.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape' || !search.value) return;
            e.preventDefault();
            e.stopPropagation();
            search.value = '';
            state.query = '';
            loadList();
        });
        $('btn-memory-more')?.addEventListener('click', () => loadList({ append: true }));
        $('btn-memory-export')?.addEventListener('click', exportMemory);
        $('btn-memory-consolidate')?.addEventListener('click', (e) => consolidate(e.currentTarget));
        $('cb-learning-enabled')?.addEventListener('change', (e) => {
            saveConfig('learning', 'enabled', e.target.checked,
                e.target.checked ? 'El agente vuelve a aprender de las conversaciones.' : 'El agente deja de guardar recuerdos nuevos.');
        });
        $('select-profile-build')?.addEventListener('change', (e) => {
            saveConfig('onboarding', 'profile_build', e.target.value, 'Preferencia de perfil guardada.');
        });
        const confirmInput = $('input-memory-forget-confirm');
        confirmInput?.addEventListener('input', () => {
            $('btn-memory-forget-all').disabled = confirmInput.value.trim().toUpperCase() !== CONFIRM_WORD;
        });
        confirmInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                forgetAll();
            }
        });
        $('btn-memory-forget-all')?.addEventListener('click', forgetAll);
        renderFilters();
    }

    document.addEventListener('gmini:settings-page', (e) => {
        if (e.detail?.page !== 'memory' || !e.detail.open) return;
        if (!state.loaded) {
            state.loaded = true;
            loadProfilePreference();
            loadList();
        }
        loadStats();
    });

    bind();
    window.gminiMemory = {
        reload: () => Promise.all([loadStats(), loadList()]),
        // Para QA visual: pinta recuerdos de ejemplo sin tocar la base real.
        preview: (items) => {
            state.items = Array.isArray(items) ? items : [];
            state.hasMore = false;
            renderList();
        },
    };
})();
