/**
 * G-Mini Agent — Configuración > Dispositivos > Este equipo.
 *
 * Que otros dispositivos (un teléfono, una placa, otra PC u otro G-Mini) se
 * conecten a este:
 *  - código de un solo uso (POST /api/v1/pairing, vence a los 5 minutos) con
 *    cuenta regresiva y QR (vendor/qrcode-generator.js, dibujado como SVG);
 *  - dispositivos con acceso (GET /api/v1/devices) y revocación
 *    (DELETE /api/v1/devices/{id}).
 * Las llamadas llevan el token de sesión de la app (api-auth.js), que tiene
 * todos los alcances.
 *
 * Con server.host en 127.0.0.1 (lo normal) ningún otro equipo llega a este PC.
 * "Permitir conexiones", con Tailscale, guarda server.extra_hosts = [su IP]: el
 * núcleo sigue en 127.0.0.1 (la app) y escucha además ahí, sin abrir las demás
 * redes. Sin Tailscale guarda server.host = 0.0.0.0, con advertencia. Los dos
 * se aplican al reiniciar el núcleo, igual que "Volver a solo este equipo".
 */
(function () {
    'use strict';

    const API = 'http://127.0.0.1:8765/api';
    const LOOPBACK = new Set(['127.0.0.1', 'localhost', '::1']);
    const ALL_INTERFACES = new Set(['0.0.0.0', '::']);
    const SCOPES = { chat: 'chat', voice: 'voz', tasks: 'tareas', node: 'nodo', admin: 'admin' };
    const TYPE_ICONS = {
        phone: 'monitor-smartphone', android: 'monitor-smartphone', ios: 'monitor-smartphone',
        desktop: 'laptop', esp32: 'cpu', cli: 'terminal',
    };
    const TYPE_LABELS = { phone: 'teléfono', desktop: 'PC', esp32: 'placa ESP32', cli: 'terminal', custom: 'dispositivo' };
    const CODE_TTL_MS = 5 * 60 * 1000;
    const POLL_MS = 3000;

    const state = {
        configLoaded: false,
        effectiveHost: '',     // server.host con el que arrancó el núcleo
        effectiveExtras: [],   // server.extra_hosts con los que arrancó
        savedHost: '',         // lo guardado ahora (se aplica al reiniciar)
        savedExtras: [],
        port: 8765,
        addresses: [],
        devices: [],
        confirming: false,
        code: null,            // {code, expiresAt, payload, knownIds}
        tick: null,
        poll: null,
    };
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

    /** La API v1 responde los errores como {error: {code, message}}. */
    async function api(path, options = {}) {
        const resp = await fetch(`${API}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!resp.ok) {
            const message = data?.error?.message || data?.detail || (typeof data?.error === 'string' ? data.error : '');
            throw new Error(message || `HTTP ${resp.status}`);
        }
        return data || {};
    }

    const isLoopback = (host) => LOOPBACK.has(String(host || '').toLowerCase());

    function relative(epochSeconds) {
        const value = Number(epochSeconds);
        if (!Number.isFinite(value) || value <= 0) return '';
        const diff = Math.max(0, Date.now() / 1000 - value);
        if (diff < 60) return 'hace un momento';
        if (diff < 3600) return `hace ${Math.floor(diff / 60)} min`;
        if (diff < 86400) return `hace ${Math.floor(diff / 3600)} h`;
        const days = Math.floor(diff / 86400);
        if (days === 1) return 'ayer';
        if (days < 30) return `hace ${days} días`;
        return new Date(value * 1000).toLocaleDateString('es', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    const sameList = (a, b) => [...a].sort().join(',') === [...b].sort().join(',');
    const tailscale = () => state.addresses.find((a) => a.tailscale) || null;
    const present = (ips) => ips.filter((ip) => state.addresses.some((a) => a.address === ip));
    const isTailscaleIp = (ip) => !!state.addresses.find((a) => a.address === ip)?.tailscale;

    /**
     * Cómo escucha el núcleo con esa config: 'local' (solo 127.0.0.1), 'extra'
     * (127.0.0.1 más IPs como la de Tailscale), 'extra-missing' (configuradas
     * pero ausentes en este equipo: queda en local) o 'all' (0.0.0.0).
     */
    function modeOf(host, extras) {
        if (!isLoopback(host)) return 'all';
        if (present(extras).length) return 'extra';
        return extras.length ? 'extra-missing' : 'local';
    }

    /** Dirección que debe usar el otro equipo, o '' si hoy no puede llegar. */
    function reachableHost() {
        const mode = modeOf(state.effectiveHost, state.effectiveExtras);
        if (mode === 'extra') return present(state.effectiveExtras)[0];
        if (mode !== 'all') return '';
        if (!ALL_INTERFACES.has(state.effectiveHost)) return state.effectiveHost;
        return state.addresses[0]?.address || '';
    }

    // ── Conexión: solo este equipo o también otros ─────────────

    async function loadAccess() {
        try {
            const [config, addresses] = await Promise.all([
                api('/config/server').catch(() => null),
                Promise.resolve(window.gmini?.getNetworkAddresses?.()).catch(() => []),
            ]);
            const server = config?.data?.server || {};
            const host = String(server.host || '127.0.0.1');
            const extras = (Array.isArray(server.extra_hosts) ? server.extra_hosts : [])
                .map((h) => String(h).trim()).filter(Boolean);
            if (!state.configLoaded) {
                state.effectiveHost = host;
                state.effectiveExtras = extras;
            }
            state.savedHost = host;
            state.savedExtras = extras;
            state.port = Number(server.port) || 8765;
            state.addresses = Array.isArray(addresses) ? addresses : [];
            state.configLoaded = true;
        } catch (err) {
            state.addresses = [];
        }
        renderAccess();
    }

    function statusCopy(mode) {
        const host = state.effectiveHost || '127.0.0.1';
        const ips = present(state.effectiveExtras);
        if (mode === 'all') {
            return ['Acepta conexiones de otros equipos',
                `G-Mini escucha en ${host} (todas las redes de este PC). Cada dispositivo necesita un código de emparejamiento y todas las rutas exigen token.`];
        }
        if (mode === 'extra') {
            const viaTailscale = ips.every(isTailscaleIp);
            return [viaTailscale ? 'Acepta conexiones por Tailscale' : 'Acepta conexiones en direcciones elegidas',
                `G-Mini escucha en 127.0.0.1 y en ${ips.join(', ')}. ${viaTailscale ? 'Solo los equipos de tu red Tailscale pueden llegar. ' : ''}Cada dispositivo necesita un código y todas las rutas exigen token.`];
        }
        if (mode === 'extra-missing') {
            return ['Solo este equipo por ahora',
                `Está configurado para escuchar también en ${state.effectiveExtras.join(', ')}, pero esa dirección no está activa en este equipo (¿Tailscale apagado?). Enciéndelo y reinicia G-Mini.`];
        }
        return ['Solo este equipo',
            `G-Mini escucha en ${host}: un teléfono u otra PC no llega a este equipo, salvo que uses tu propio túnel o proxy. Para emparejar desde otro dispositivo, permite conexiones en las opciones avanzadas.`];
    }

    function renderAccess() {
        const box = $('access-status');
        if (!box) return;
        const mode = modeOf(state.effectiveHost, state.effectiveExtras);
        const open = mode === 'all' || mode === 'extra';
        box.className = `access-status ${open ? 'is-open' : 'is-local'}`;
        box.replaceChildren();
        const tile = el('span', 'access-status-icon');
        tile.innerHTML = icon(open ? 'network' : 'shield');
        const [title, body] = statusCopy(mode);
        const text = el('div', 'access-status-text');
        text.append(el('strong', '', title), el('span', '', body));
        box.append(tile, text);

        // Direcciones útiles para el otro equipo: todas con 0.0.0.0, solo las extra si no.
        const list = $('access-addresses');
        if (list) {
            const shown = mode === 'all'
                ? state.addresses
                : present(state.effectiveExtras).map((ip) => state.addresses.find((a) => a.address === ip));
            list.replaceChildren();
            list.hidden = !open || !shown.length;
            shown.forEach((item, index) => {
                const chip = el('span', `access-address${item.tailscale ? ' is-tailscale' : ''}`);
                chip.append(
                    el('span', 'access-address-kind', item.tailscale ? 'Tailscale' : 'Red local'),
                    el('code', '', `${item.address}:${state.port}`),
                );
                if (index === 0 && item.tailscale && mode === 'all') chip.appendChild(el('span', 'access-address-note', 'recomendada'));
                chip.title = item.name;
                list.appendChild(chip);
            });
        }
        renderAdvanced();
        renderRestart();
    }

    function advancedRow(text, label, iconName, onClick) {
        const row = el('div', 'access-advanced-row');
        row.appendChild(el('p', 'setting-help-text', text));
        const button = el('button', 'btn-secondary btn-panel-action');
        button.type = 'button';
        button.innerHTML = `${icon(iconName)}<span>${label}</span>`;
        button.addEventListener('click', onClick);
        row.appendChild(button);
        return row;
    }

    /** Confirmación: con Tailscale es informativa; con 0.0.0.0, advertencia. */
    function confirmBlock({ warning, title, text, confirmLabel, onConfirm }) {
        const box = el('div', warning ? 'access-warning' : 'access-note');
        box.setAttribute('role', warning ? 'alert' : 'note');
        box.innerHTML = icon(warning ? 'triangle-alert' : 'info');
        const words = el('div', 'access-warning-text');
        words.append(el('strong', '', title), el('span', '', text));
        box.appendChild(words);
        const actions = el('div', 'connector-buttons');
        const confirm = el('button', 'btn-primary-accent');
        confirm.type = 'button';
        confirm.id = 'btn-access-confirm';
        confirm.innerHTML = `${icon('check')}<span>${confirmLabel}</span>`;
        confirm.addEventListener('click', onConfirm);
        const cancel = el('button', 'btn-secondary btn-panel-action', 'Cancelar');
        cancel.type = 'button';
        cancel.addEventListener('click', () => {
            state.confirming = false;
            renderAdvanced();
        });
        actions.append(confirm, cancel);
        return [box, actions];
    }

    function renderAdvanced() {
        const body = $('access-advanced-body');
        if (!body) return;
        body.replaceChildren();
        // Lo guardado manda: si ya se pidió abrir, se ofrece volver atrás.
        if (modeOf(state.savedHost, state.savedExtras) !== 'local') {
            body.appendChild(advancedRow(
                'Vuelve a aceptar solo conexiones de este mismo equipo. Los dispositivos emparejados conservan su acceso, pero no podrán llegar hasta que lo vuelvas a permitir.',
                'Volver a solo este equipo', 'shield', () => saveAccess('127.0.0.1', []),
            ));
            return;
        }
        const ts = tailscale();
        if (!state.confirming) {
            body.appendChild(ts
                ? advancedRow(`Para que tus otros equipos con Tailscale lleguen a este PC por ${ts.address}. Las demás redes siguen cerradas.`,
                    'Permitir conexiones por Tailscale', 'network', () => startConfirm())
                : advancedRow('Para que otros dispositivos lleguen a este PC. Lo más seguro es instalar Tailscale en los dos equipos: así solo tu red privada puede llegar.',
                    'Permitir conexiones de otros equipos', 'network', () => startConfirm()));
            return;
        }
        body.append(...(ts
            ? confirmBlock({
                warning: false,
                title: `G-Mini escuchará también en ${ts.address} (Tailscale).`,
                text: 'Solo los equipos de tu red Tailscale podrán llegar; el resto de redes sigue cerrado. Cada dispositivo necesita un código y todas las rutas exigen token.',
                confirmLabel: 'Sí, permitir por Tailscale',
                onConfirm: () => saveAccess('127.0.0.1', [ts.address]),
            })
            : confirmBlock({
                warning: true,
                title: 'G-Mini escuchará en todas las redes de este PC (0.0.0.0).',
                text: 'Todas las rutas exigen token y cada dispositivo necesita un código, pero úsalo solo en redes de confianza: tu casa, tu oficina o Tailscale. En una Wi-Fi pública cualquiera podría intentar conectarse. Windows puede pedirte que permitas el acceso en el firewall.',
                confirmLabel: 'Sí, permitir conexiones',
                onConfirm: () => saveAccess('0.0.0.0', []),
            })));
    }

    function startConfirm() {
        state.confirming = true;
        renderAdvanced();
        $('btn-access-confirm')?.focus();
    }

    /** Guarda los dos valores juntos: el modo depende de host y de extra_hosts. */
    async function saveAccess(host, extras) {
        const settings = window.settingsManager;
        const okHost = await settings?._saveConfigValue?.('server', 'host', host);
        const okExtras = okHost && await settings?._saveConfigValue?.('server', 'extra_hosts', extras);
        if (!okHost || !okExtras) {
            toast('No se pudo guardar cómo escucha G-Mini.', true);
            await loadAccess();
            return;
        }
        state.savedHost = host;
        state.savedExtras = extras;
        state.confirming = false;
        renderAccess();
        $('btn-access-restart')?.focus();
    }

    function renderRestart() {
        const bar = $('access-restart');
        if (!bar) return;
        const pending = state.configLoaded
            && (state.savedHost !== state.effectiveHost || !sameList(state.savedExtras, state.effectiveExtras));
        bar.hidden = !pending;
        const text = $('access-restart-text');
        if (!text || !pending) return;
        const mode = modeOf(state.savedHost, state.savedExtras);
        text.textContent = {
            local: 'Guardado: al reiniciar, G-Mini aceptará solo conexiones de este equipo.',
            extra: `Guardado: al reiniciar, G-Mini aceptará conexiones por ${state.savedExtras.join(', ')}.`,
            'extra-missing': `Guardado: al reiniciar, G-Mini escuchará también en ${state.savedExtras.join(', ')} si esa dirección está activa.`,
            all: 'Guardado: al reiniciar, G-Mini aceptará conexiones de otros equipos en todas las redes.',
        }[mode];
    }

    // ── Código de emparejamiento ───────────────────────────────

    async function generate(e) {
        e.preventDefault();
        const status = $('pairing-status');
        const button = $('btn-pairing-generate');
        const scopes = Array.from(document.querySelectorAll('#pairing-form input[name="pairing-scope"]:checked')).map((i) => i.value);
        status.classList.remove('is-error');
        if (!scopes.length) {
            status.classList.add('is-error');
            status.textContent = 'Elige al menos un permiso.';
            return;
        }
        button.disabled = true;
        status.textContent = '';
        try {
            const knownIds = new Set(state.devices.map((d) => d.id));
            const data = await api('/v1/pairing', {
                method: 'POST',
                body: JSON.stringify({
                    label: $('pairing-label').value.trim(),
                    device_type: $('pairing-type').value || 'custom',
                    scopes,
                }),
            });
            const expiresAt = Date.parse(data.expires_at) || Date.now() + CODE_TTL_MS;
            state.code = { code: String(data.code || ''), expiresAt, payload: String(data.qr_payload || ''), knownIds };
            showCode();
        } catch (err) {
            status.classList.add('is-error');
            status.textContent = `No se pudo generar el código: ${err.message}`;
        } finally {
            button.disabled = false;
        }
    }

    function qrPayload() {
        const host = reachableHost();
        if (!host || !state.code) return '';
        try {
            const url = new URL(state.code.payload);
            url.searchParams.set('host', host);
            url.searchParams.set('port', String(state.port));
            return url.toString();
        } catch (err) {
            return `gmini://pair?host=${encodeURIComponent(host)}&port=${state.port}&code=${state.code.code}`;
        }
    }

    /** SVG del QR armado con DOM (sin innerHTML): módulos oscuros sobre fondo blanco. */
    function qrSvg(text) {
        if (typeof qrcode !== 'function' || !text) return null;
        const qr = qrcode(0, 'M');
        qr.addData(text);
        qr.make();
        const count = qr.getModuleCount();
        const margin = 4;
        const size = count + margin * 2;
        const NS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
        svg.setAttribute('shape-rendering', 'crispEdges');
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', 'Código QR para emparejar este equipo');
        const background = document.createElementNS(NS, 'rect');
        background.setAttribute('width', String(size));
        background.setAttribute('height', String(size));
        background.setAttribute('fill', '#ffffff');
        let d = '';
        for (let row = 0; row < count; row += 1) {
            for (let col = 0; col < count; col += 1) {
                if (qr.isDark(row, col)) d += `M${col + margin} ${row + margin}h1v1h-1z`;
            }
        }
        const modules = document.createElementNS(NS, 'path');
        modules.setAttribute('d', d);
        modules.setAttribute('fill', '#111111');
        svg.append(background, modules);
        return svg;
    }

    function showCode() {
        const box = $('pairing-result');
        if (!box || !state.code) return;
        const digits = state.code.code;
        $('pairing-code').textContent = digits.length === 6 ? `${digits.slice(0, 3)} ${digits.slice(3)}` : digits;
        $('pairing-code').setAttribute('aria-label', `Código ${digits.split('').join(' ')}`);
        box.classList.remove('is-expired');
        box.hidden = false;

        const host = reachableHost();
        const hint = $('pairing-hint');
        hint.textContent = host
            ? `En el otro dispositivo usa la dirección ${host}:${state.port} y este código, o escanea el QR.`
            : 'Este equipo solo acepta conexiones locales: el código sirve si llegas por tu propio túnel o proxy. Para usarlo desde otro equipo, permite conexiones arriba.';
        const qrBox = $('pairing-qr');
        const svgSlot = $('pairing-qr-svg');
        const payload = qrPayload();
        svgSlot.replaceChildren();
        const svg = qrSvg(payload);
        qrBox.hidden = !svg;
        if (svg) svgSlot.appendChild(svg);

        clearInterval(state.tick);
        updateCountdown();
        state.tick = setInterval(updateCountdown, 1000);
        clearInterval(state.poll);
        state.poll = setInterval(watchForDevice, POLL_MS);
    }

    function updateCountdown() {
        if (!state.code) return;
        const left = Math.max(0, state.code.expiresAt - Date.now());
        const seconds = Math.ceil(left / 1000);
        const label = $('pairing-countdown');
        const fill = $('pairing-bar-fill');
        if (fill) fill.style.transform = `scaleX(${Math.min(1, left / CODE_TTL_MS)})`;
        if (left <= 0) {
            clearInterval(state.tick);
            clearInterval(state.poll);
            $('pairing-result')?.classList.add('is-expired');
            if (label) label.textContent = 'Venció. Genera otro código.';
            return;
        }
        if (label) label.textContent = `Vence en ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }

    /** Mientras el código está vigente: si aparece un dispositivo nuevo, se usó. */
    async function watchForDevice() {
        if (!state.code) return;
        await loadDevices();
        const fresh = state.devices.find((d) => !state.code.knownIds.has(d.id));
        if (!fresh) return;
        clearInterval(state.tick);
        clearInterval(state.poll);
        state.code = null;
        $('pairing-result').hidden = true;
        const status = $('pairing-status');
        status.classList.remove('is-error');
        status.textContent = `Se conectó ${fresh.name || 'un dispositivo nuevo'}.`;
        $('access-devices-list')?.querySelector(`[data-id="${CSS.escape(fresh.id)}"]`)?.classList.add('is-new');
    }

    // ── Dispositivos con acceso ────────────────────────────────

    async function loadDevices() {
        const list = $('access-devices-list');
        try {
            const data = await api('/v1/devices');
            state.devices = Array.isArray(data.items) ? data.items : [];
            renderDevices();
        } catch (err) {
            if (!list) return;
            list.replaceChildren(emptyState(`No se pudo leer la lista: ${err.message}`));
        }
    }

    function emptyState(text) {
        const empty = el('div', 'memory-empty');
        empty.innerHTML = icon('monitor-smartphone');
        empty.appendChild(el('span', '', text));
        return empty;
    }

    function renderDevices() {
        const list = $('access-devices-list');
        if (!list) return;
        list.replaceChildren();
        const devices = state.devices.slice().sort((a, b) =>
            (Number(b.last_seen_at) || Number(b.created_at) || 0) - (Number(a.last_seen_at) || Number(a.created_at) || 0));
        if (!devices.length) {
            list.appendChild(emptyState('Ningún dispositivo tiene acceso todavía. Genera un código para conectar el primero.'));
            return;
        }
        devices.forEach((device) => list.appendChild(renderDevice(device)));
    }

    function renderDevice(device) {
        const card = el('article', 'connector-card device-card');
        card.dataset.id = device.id;
        const head = el('div', 'connector-head');
        const tile = el('span', 'connector-icon');
        tile.setAttribute('aria-hidden', 'true');
        const apiToken = device.kind === 'api';
        tile.innerHTML = icon(apiToken ? 'key-round' : (TYPE_ICONS[device.device_type] || 'plug'));

        const main = el('div', 'connector-main');
        const title = el('div', 'connector-title');
        title.appendChild(el('h5', 'connector-name', device.name || device.id));
        if (apiToken) title.appendChild(el('span', 'skill-chip is-muted', 'token de API'));
        main.appendChild(title);
        const kind = [TYPE_LABELS[device.device_type] || device.device_type, device.platform].filter(Boolean).join(' · ');
        if (kind) main.appendChild(el('p', 'connector-desc', kind));

        const meta = el('div', 'connector-meta');
        const seen = relative(device.last_seen_at);
        meta.appendChild(el('span', '', seen ? `último uso ${seen}` : 'sin usar todavía'));
        const created = relative(device.created_at);
        if (created) meta.appendChild(el('span', '', `emparejado ${created}`));
        const scopes = el('span', 'device-scopes');
        (Array.isArray(device.scopes) ? device.scopes : []).forEach((scope) => {
            scopes.appendChild(el('span', 'skill-chip is-muted', SCOPES[scope] || scope));
        });
        if (scopes.childElementCount) meta.appendChild(scopes);
        main.appendChild(meta);

        head.append(tile, main, revokeButton(device));
        card.appendChild(head);
        return card;
    }

    /** Segundo clic para confirmar: el dispositivo tendrá que emparejarse de nuevo. */
    function revokeButton(device) {
        const button = el('button', 'btn-secondary btn-panel-action');
        button.type = 'button';
        const idle = () => {
            button.classList.remove('btn-danger');
            button.innerHTML = `${icon('unlink')}<span>Revocar</span>`;
            button.setAttribute('aria-label', `Revocar el acceso de ${device.name || device.id}`);
        };
        idle();
        button.addEventListener('click', async () => {
            if (!button.classList.contains('btn-danger')) {
                button.classList.add('btn-danger');
                button.innerHTML = `${icon('triangle-alert')}<span>Confirmar</span>`;
                button.setAttribute('aria-label', `Confirmar: revocar el acceso de ${device.name || device.id}`);
                clearTimeout(button._timer);
                button._timer = setTimeout(idle, 4000);
                return;
            }
            clearTimeout(button._timer);
            button.disabled = true;
            try {
                await api(`/v1/devices/${encodeURIComponent(device.id)}`, { method: 'DELETE' });
                toast(`Se revocó el acceso de ${device.name || 'ese dispositivo'}. Para volver a usarlo, emparéjalo otra vez.`);
                await loadDevices();
            } catch (err) {
                button.disabled = false;
                idle();
                toast(`No se pudo revocar: ${err.message}`, true);
            }
        });
        return button;
    }

    // ── Arranque ───────────────────────────────────────────────

    $('pairing-form')?.addEventListener('submit', generate);
    $('btn-devices-refresh')?.addEventListener('click', () => loadDevices());
    $('btn-access-restart')?.addEventListener('click', () => window.gmini?.relaunch?.());

    document.addEventListener('gmini:settings-page', (e) => {
        if (e.detail?.page !== 'devices' || !e.detail.open) return;
        loadAccess();
        loadDevices();
    });

    window.gminiDeviceAccess = { reload: () => Promise.all([loadAccess(), loadDevices()]) };
})();
