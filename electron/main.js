/**
 * G-Mini Agent — Electron Main Process
 * Ventana principal + overlay transparente + tray.
 * Lanza el backend Python como proceso hijo automáticamente.
 */

const electronModule = require('electron');
if (!electronModule || typeof electronModule !== 'object' || !electronModule.app) {
    const runAsNode = String(process.env.ELECTRON_RUN_AS_NODE || '').trim();
    console.error(
        '[App] No se pudo iniciar Electron porque el proceso se esta ejecutando en modo Node. '
        + "require('electron') no expuso 'app'. "
        + (runAsNode ? `ELECTRON_RUN_AS_NODE=${runAsNode}. ` : '')
        + 'Abre un shell limpio o elimina esa variable antes de iniciar la app.'
    );
    process.exit(1);
}

const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, screen, dialog, protocol, net } = electronModule;
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const yaml = require('js-yaml');

protocol.registerSchemesAsPrivileged([
    {
        scheme: 'gmini-skin',
        privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
    },
]);

let mainWindow = null;
let overlayWindow = null;
let skinWindow = null;
let skinChatSavedBounds = null;
let tray = null;
let isOverlayMode = false;
let currentSkinMode = 'chat';
let backendProcess = null;
let backendProcessOwnership = 'none';
let lastOverlayText = 'G-Mini Agent listo';

const BACKEND_URL = 'http://127.0.0.1:8765';
const BACKEND_HEALTH_URL = `${BACKEND_URL}/api/health`;
const BACKEND_START_TIMEOUT_MS = 60000;
const BACKEND_HEALTH_CHECK_INTERVAL_MS = 1000;
const BACKEND_HEALTH_REQUEST_TIMEOUT_MS = 1500;
const PROJECT_ROOT = path.resolve(__dirname, '..');
const DEFAULT_CONFIG_PATH = path.join(PROJECT_ROOT, 'config.default.yaml');
const USER_CONFIG_PATH = path.join(PROJECT_ROOT, 'config.user.yaml');
const OVERLAY_STATE_FILENAME = 'overlay-state.json';
const OVERLAY_BASE_SIZE = Object.freeze({ width: 400, height: 300 });
const OVERLAY_MIN_SCALE = 0.7;
const OVERLAY_MAX_SCALE = 2.25;
const OVERLAY_SAVE_DEBOUNCE_MS = 180;
const SKIN_STATE_FILENAME = 'skin-state.json';
const SKIN_BASE_SIZE = Object.freeze({ width: 320, height: 360 });
const SKIN_MIN_SCALE = 0.5;
const SKIN_MAX_SCALE = 2.5;
const SKIN_SAVE_DEBOUNCE_MS = 180;
const SKIN_CHAT_BUBBLE_WIDTH = 300;
const DATA_SKINS_DIR = path.join(PROJECT_ROOT, 'data', 'skins');
const CHARACTER_EMOTIONS = Object.freeze(['neutral', 'happy', 'sad', 'angry', 'surprised', 'relaxed']);
const SINGLE_INSTANCE_LOCK = app.requestSingleInstanceLock();

if (!SINGLE_INSTANCE_LOCK) {
    console.error('[App] Ya hay una instancia activa de G-Mini Agent. Cerrando la nueva instancia.');
    process.exit(0);
}

const DEFAULT_APP_PREFERENCES = Object.freeze({
    startWithWindows: false,
    minimizeToTray: true,
    closeToTray: true,
    startHiddenToTray: false,
});
const DEFAULT_CHARACTER_PREFERENCES = Object.freeze({
    type: '3d',
    skin: 'energy-ball',
    mode: 'chat',
    emotionsEnabled: false,
    defaultSize: 200,
    defaultOpacity: 100,
    blinkIntervalMinMs: 3000,
    blinkIntervalMaxMs: 5000,
    blinkDurationMs: 150,
});
const DEFAULT_OVERLAY_CHARACTER_RUNTIME = Object.freeze({
    status: 'idle',
    audioHintMs: 0,
    visemes: [],
    mouth: 0,
    emotion: 'neutral',
    updatedAt: 0,
});

let appPreferences = { ...DEFAULT_APP_PREFERENCES };
let characterPreferences = { ...DEFAULT_CHARACTER_PREFERENCES };
let configReloadTimer = null;
let overlayRuntimeState = null;
let overlayStateSaveTimer = null;
let overlayInteractionLocked = false;
let overlayCharacterRuntime = { ...DEFAULT_OVERLAY_CHARACTER_RUNTIME };
let skinRuntimeState = null;
let skinStateSaveTimer = null;

function deepMerge(base, override) {
    const merged = { ...(base || {}) };
    for (const [key, value] of Object.entries(override || {})) {
        if (
            Object.prototype.hasOwnProperty.call(merged, key)
            && typeof merged[key] === 'object'
            && merged[key] !== null
            && !Array.isArray(merged[key])
            && typeof value === 'object'
            && value !== null
            && !Array.isArray(value)
        ) {
            merged[key] = deepMerge(merged[key], value);
        } else {
            merged[key] = value;
        }
    }
    return merged;
}

function readYamlConfig(filePath) {
    try {
        if (!fs.existsSync(filePath)) return {};
        return yaml.load(fs.readFileSync(filePath, 'utf8')) || {};
    } catch (err) {
        console.warn(`[Config] No se pudo leer ${path.basename(filePath)}: ${err.message}`);
        return {};
    }
}

function normalizeAppPreferences(rawAppConfig = {}) {
    return {
        startWithWindows: !!rawAppConfig.start_with_windows,
        minimizeToTray: rawAppConfig.minimize_to_tray !== false,
        closeToTray: rawAppConfig.close_to_tray !== false,
        startHiddenToTray: !!rawAppConfig.start_hidden_to_tray,
    };
}

function loadMergedProjectConfigFromDisk() {
    const defaults = readYamlConfig(DEFAULT_CONFIG_PATH);
    const overrides = readYamlConfig(USER_CONFIG_PATH);
    return deepMerge(defaults, overrides);
}

function normalizeCharacterPreferences(rawCharacterConfig = {}) {
    const defaultSize = clampNumber(toFiniteNumber(rawCharacterConfig.default_size, DEFAULT_CHARACTER_PREFERENCES.defaultSize), 120, 420);
    const defaultOpacity = clampNumber(toFiniteNumber(rawCharacterConfig.default_opacity, DEFAULT_CHARACTER_PREFERENCES.defaultOpacity), 35, 100);
    const blinkIntervalMinMs = clampNumber(
        Math.round(toFiniteNumber(rawCharacterConfig.blink_interval_min_s, DEFAULT_CHARACTER_PREFERENCES.blinkIntervalMinMs / 1000) * 1000),
        1200,
        12000
    );
    const blinkIntervalMaxMs = clampNumber(
        Math.round(toFiniteNumber(rawCharacterConfig.blink_interval_max_s, DEFAULT_CHARACTER_PREFERENCES.blinkIntervalMaxMs / 1000) * 1000),
        blinkIntervalMinMs,
        16000
    );
    const blinkDurationMs = clampNumber(
        Math.round(toFiniteNumber(rawCharacterConfig.blink_duration_ms, DEFAULT_CHARACTER_PREFERENCES.blinkDurationMs)),
        60,
        900
    );

    let type = String(rawCharacterConfig.type || DEFAULT_CHARACTER_PREFERENCES.type);
    if (!['3d', '2d', 'none'].includes(type)) type = DEFAULT_CHARACTER_PREFERENCES.type;
    const skin = String(rawCharacterConfig.skin || DEFAULT_CHARACTER_PREFERENCES.skin);
    // La bola de energia es procedural/3D, nunca 2D (configs viejas la guardaban como 2D).
    if (skin === 'energy-ball' && type === '2d') type = '3d';

    return {
        type,
        skin,
        mode: rawCharacterConfig.mode === 'skin' ? 'skin' : 'chat',
        emotionsEnabled: !!rawCharacterConfig.emotions_enabled,
        defaultSize,
        defaultOpacity,
        blinkIntervalMinMs,
        blinkIntervalMaxMs,
        blinkDurationMs,
    };
}

function loadAppPreferencesFromDisk() {
    const merged = loadMergedProjectConfigFromDisk();
    return normalizeAppPreferences(merged.app || {});
}

function loadCharacterPreferencesFromDisk() {
    const merged = loadMergedProjectConfigFromDisk();
    return normalizeCharacterPreferences(merged.character || {});
}

function getEffectiveAppRuntimeSettings() {
    const canApplyStartWithWindows = process.platform === 'win32' && app.isPackaged;
    let loginItemEnabled = false;

    try {
        if (process.platform === 'win32') {
            loginItemEnabled = !!app.getLoginItemSettings().openAtLogin;
        }
    } catch (err) {
        loginItemEnabled = false;
    }

    return {
        ...appPreferences,
        canApplyStartWithWindows,
        startWithWindowsApplied: canApplyStartWithWindows ? loginItemEnabled : false,
        isPackaged: app.isPackaged,
        platform: process.platform,
    };
}

function applyStartWithWindowsSetting() {
    if (process.platform !== 'win32') return;

    if (!app.isPackaged) {
        if (appPreferences.startWithWindows) {
            console.warn('[App] Inicio con Windows solicitado, pero solo se aplica automaticamente en builds empaquetadas.');
        }
        return;
    }

    try {
        app.setLoginItemSettings({
            openAtLogin: !!appPreferences.startWithWindows,
        });
    } catch (err) {
        console.error(`[App] No se pudo actualizar inicio con Windows: ${err.message}`);
    }
}

function refreshTrayMenu() {
    if (!tray) return;

    const behaviorSummary = appPreferences.closeToTray
        ? 'Cerrar -> bandeja'
        : 'Cerrar -> salir';
    const minimizeSummary = appPreferences.minimizeToTray
        ? 'Minimizar -> bandeja'
        : 'Minimizar normal';

    const contextMenu = Menu.buildFromTemplate([
        {
            label: 'Mostrar G-Mini Agent',
            click: () => {
                setSkinMode('chat');
                if (mainWindow) {
                    mainWindow.show();
                    mainWindow.focus();
                }
            },
        },
        {
            label: behaviorSummary,
            enabled: false,
        },
        {
            label: minimizeSummary,
            enabled: false,
        },
        {
            label: 'Modo Overlay',
            type: 'checkbox',
            checked: isOverlayMode,
            click: (item) => {
                toggleOverlay(item.checked);
            },
        },
        {
            label: currentSkinMode === 'skin' ? 'Mostrar avatar' : 'Avatar flotante',
            type: 'checkbox',
            checked: currentSkinMode === 'skin' && !!(skinWindow && !skinWindow.isDestroyed() && skinWindow.isVisible()),
            click: (item) => {
                if (item.checked) {
                    setSkinMode('skin');
                } else if (currentSkinMode === 'skin' && skinWindow && !skinWindow.isDestroyed()) {
                    skinWindow.hide();
                    refreshTrayMenu();
                } else {
                    setSkinMode('chat');
                }
            },
        },
        { type: 'separator' },
        {
            label: 'Salir',
            click: () => {
                app.isQuitting = true;
                app.quit();
            },
        },
    ]);

    tray.setContextMenu(contextMenu);
}

function applyAppPreferences(nextPreferences = null) {
    appPreferences = {
        ...DEFAULT_APP_PREFERENCES,
        ...(nextPreferences || loadAppPreferencesFromDisk()),
    };

    if (app.isReady()) {
        applyStartWithWindowsSetting();
        refreshTrayMenu();
    }
}

function applyCharacterPreferences(nextPreferences = null) {
    characterPreferences = {
        ...DEFAULT_CHARACTER_PREFERENCES,
        ...(nextPreferences || loadCharacterPreferencesFromDisk()),
    };
    broadcastOverlayState();
    broadcastSkinState();
}

function reloadRuntimePreferencesFromDisk() {
    const mergedConfig = loadMergedProjectConfigFromDisk();
    applyAppPreferences(normalizeAppPreferences(mergedConfig.app || {}));
    applyCharacterPreferences(normalizeCharacterPreferences(mergedConfig.character || {}));
}

function scheduleAppPreferencesReload() {
    clearTimeout(configReloadTimer);
    configReloadTimer = setTimeout(() => {
        try {
            reloadRuntimePreferencesFromDisk();
        } catch (err) {
            console.error(`[Config] Error recargando preferencias app: ${err.message}`);
        }
    }, 150);
}

function watchConfigFiles() {
    fs.watchFile(DEFAULT_CONFIG_PATH, { interval: 1200 }, scheduleAppPreferencesReload);
    fs.watchFile(USER_CONFIG_PATH, { interval: 1200 }, scheduleAppPreferencesReload);
}

function unwatchConfigFiles() {
    fs.unwatchFile(DEFAULT_CONFIG_PATH, scheduleAppPreferencesReload);
    fs.unwatchFile(USER_CONFIG_PATH, scheduleAppPreferencesReload);
}

reloadRuntimePreferencesFromDisk();

function getOverlayStatePath() {
    return path.join(app.getPath('userData'), OVERLAY_STATE_FILENAME);
}

function toFiniteNumber(value, fallback) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : fallback;
}

function clampNumber(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

function getOverlayDisplayBounds(display) {
    if (display?.workArea && Number.isFinite(display.workArea.width) && Number.isFinite(display.workArea.height)) {
        return display.workArea;
    }
    if (display?.bounds) return display.bounds;
    return screen.getPrimaryDisplay().workArea;
}

function getDefaultOverlayBounds(display = null, scale = 1.0) {
    const targetDisplay = display || screen.getPrimaryDisplay();
    const workArea = getOverlayDisplayBounds(targetDisplay);
    const normalizedScale = clampNumber(toFiniteNumber(scale, 1.0), OVERLAY_MIN_SCALE, OVERLAY_MAX_SCALE);
    const width = Math.round(OVERLAY_BASE_SIZE.width * normalizedScale);
    const height = Math.round(OVERLAY_BASE_SIZE.height * normalizedScale);
    return {
        x: Math.round(workArea.x + workArea.width - width - 20),
        y: Math.round(workArea.y + workArea.height - height - 20),
        width,
        height,
    };
}

function getAllDisplaysSafe() {
    try {
        return screen.getAllDisplays();
    } catch (err) {
        console.warn(`[Overlay] No se pudo leer displays: ${err.message}`);
        return [screen.getPrimaryDisplay()];
    }
}

function getDisplayById(displayId) {
    return getAllDisplaysSafe().find((display) => display.id === displayId) || null;
}

function hasVisibleIntersection(bounds) {
    return getAllDisplaysSafe().some((display) => {
        const area = getOverlayDisplayBounds(display);
        const overlapWidth = Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x);
        const overlapHeight = Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y);
        return overlapWidth > 0 && overlapHeight > 0;
    });
}

function normalizeOverlayBounds(rawBounds, preferredDisplayId = null) {
    const preferredDisplay = preferredDisplayId !== null ? getDisplayById(preferredDisplayId) : null;
    const fallbackDisplay = preferredDisplay || screen.getPrimaryDisplay();
    const desiredWidth = toFiniteNumber(rawBounds?.width, OVERLAY_BASE_SIZE.width);
    const inferredScale = desiredWidth / OVERLAY_BASE_SIZE.width;
    const scale = clampNumber(toFiniteNumber(rawBounds?.scale, inferredScale), OVERLAY_MIN_SCALE, OVERLAY_MAX_SCALE);
    const width = Math.round(OVERLAY_BASE_SIZE.width * scale);
    const height = Math.round(OVERLAY_BASE_SIZE.height * scale);
    const rawX = Math.round(toFiniteNumber(rawBounds?.x, getDefaultOverlayBounds(fallbackDisplay, scale).x));
    const rawY = Math.round(toFiniteNumber(rawBounds?.y, getDefaultOverlayBounds(fallbackDisplay, scale).y));
    const provisionalBounds = { x: rawX, y: rawY, width, height };

    if (!hasVisibleIntersection(provisionalBounds)) {
        const resetBounds = getDefaultOverlayBounds(fallbackDisplay, scale);
        return {
            ...resetBounds,
            scale,
            displayId: fallbackDisplay.id,
        };
    }

    const matchedDisplay = screen.getDisplayMatching(provisionalBounds) || fallbackDisplay;
    const workArea = getOverlayDisplayBounds(matchedDisplay);
    const clampedWidth = Math.min(width, workArea.width);
    const clampedHeight = Math.min(height, workArea.height);
    const finalScale = clampNumber(
        Math.min(clampedWidth / OVERLAY_BASE_SIZE.width, clampedHeight / OVERLAY_BASE_SIZE.height),
        OVERLAY_MIN_SCALE,
        OVERLAY_MAX_SCALE
    );
    const finalWidth = Math.round(OVERLAY_BASE_SIZE.width * finalScale);
    const finalHeight = Math.round(OVERLAY_BASE_SIZE.height * finalScale);
    const x = clampNumber(rawX, workArea.x, workArea.x + workArea.width - finalWidth);
    const y = clampNumber(rawY, workArea.y, workArea.y + workArea.height - finalHeight);

    return {
        x,
        y,
        width: finalWidth,
        height: finalHeight,
        scale: finalScale,
        displayId: matchedDisplay.id,
    };
}

function sanitizeCharacterRuntimeUpdate(payload = {}) {
    const nextState = {
        ...overlayCharacterRuntime,
    };

    if (typeof payload.status === 'string' && payload.status.trim()) {
        nextState.status = payload.status.trim().toLowerCase();
    }

    if (Number.isFinite(Number(payload.audioHintMs))) {
        nextState.audioHintMs = Math.max(0, Math.round(Number(payload.audioHintMs)));
    }

    if (Array.isArray(payload.visemes)) {
        nextState.visemes = payload.visemes
            .filter((entry) => entry && Number.isFinite(Number(entry.time)))
            .map((entry) => ({
                time: Number(entry.time),
                viseme: String(entry.viseme || 'rest'),
                weight: Number.isFinite(Number(entry.weight)) ? Number(entry.weight) : 0.0,
            }));
    }

    if (Number.isFinite(Number(payload.mouth))) {
        nextState.mouth = clampNumber(Number(payload.mouth), 0, 1);
    }

    if (payload.emotion && typeof payload.emotion === 'object') {
        const emotionName = String(payload.emotion.name || 'neutral').trim().toLowerCase();
        nextState.emotion = CHARACTER_EMOTIONS.includes(emotionName) ? emotionName : 'neutral';
    } else if (typeof payload.emotion === 'string') {
        const emotionName = payload.emotion.trim().toLowerCase();
        nextState.emotion = CHARACTER_EMOTIONS.includes(emotionName) ? emotionName : 'neutral';
    }

    nextState.updatedAt = Number.isFinite(Number(payload.updatedAt))
        ? Number(payload.updatedAt)
        : Date.now();

    return nextState;
}

function getOverlayStateSnapshot() {
    const fallbackBounds = getDefaultOverlayBounds();
    const activeBounds = overlayWindow && !overlayWindow.isDestroyed()
        ? overlayWindow.getBounds()
        : (overlayRuntimeState?.bounds || fallbackBounds);

    return {
        interactive: !!overlayRuntimeState?.interactive && !overlayInteractionLocked,
        requestedInteractive: !!overlayRuntimeState?.interactive,
        lockedPassive: overlayInteractionLocked,
        visible: !!(overlayWindow && !overlayWindow.isDestroyed() && overlayWindow.isVisible()),
        displayId: overlayRuntimeState?.displayId ?? null,
        scale: overlayRuntimeState?.scale ?? 1.0,
        bounds: activeBounds,
        minScale: OVERLAY_MIN_SCALE,
        maxScale: OVERLAY_MAX_SCALE,
        baseWidth: OVERLAY_BASE_SIZE.width,
        baseHeight: OVERLAY_BASE_SIZE.height,
        characterConfig: characterPreferences,
        characterRuntime: overlayCharacterRuntime,
    };
}

function persistOverlayStateToDisk() {
    if (!app.isReady() || !overlayRuntimeState) return;

    try {
        fs.mkdirSync(app.getPath('userData'), { recursive: true });
        fs.writeFileSync(
            getOverlayStatePath(),
            JSON.stringify({
                bounds: overlayRuntimeState.bounds,
                scale: overlayRuntimeState.scale,
                displayId: overlayRuntimeState.displayId,
            }, null, 2),
            'utf8'
        );
    } catch (err) {
        console.error(`[Overlay] No se pudo persistir estado: ${err.message}`);
    }
}

function scheduleOverlayStatePersist() {
    clearTimeout(overlayStateSaveTimer);
    overlayStateSaveTimer = setTimeout(() => {
        persistOverlayStateToDisk();
    }, OVERLAY_SAVE_DEBOUNCE_MS);
}

function loadOverlayStateFromDisk() {
    const fallbackBounds = getDefaultOverlayBounds();
    const fallbackState = {
        interactive: false,
        scale: 1.0,
        displayId: screen.getPrimaryDisplay().id,
        bounds: fallbackBounds,
    };

    try {
        const statePath = getOverlayStatePath();
        if (!fs.existsSync(statePath)) return fallbackState;
        const rawState = JSON.parse(fs.readFileSync(statePath, 'utf8'));
        const normalizedBounds = normalizeOverlayBounds(rawState?.bounds || rawState, rawState?.displayId ?? null);
        return {
            interactive: false,
            scale: normalizedBounds.scale,
            displayId: normalizedBounds.displayId,
            bounds: {
                x: normalizedBounds.x,
                y: normalizedBounds.y,
                width: normalizedBounds.width,
                height: normalizedBounds.height,
            },
        };
    } catch (err) {
        console.warn(`[Overlay] No se pudo cargar estado persistido: ${err.message}`);
        return fallbackState;
    }
}

function updateOverlayWindowInteractivity() {
    if (!overlayWindow || overlayWindow.isDestroyed()) return;

    const shouldIgnoreMouse = overlayInteractionLocked || !overlayRuntimeState?.interactive;

    try {
        // SIN { forward: true }: en Windows forward instala un hook global de mouse que
        // rompe el arrastre de CUALQUIER ventana —incluido el avatar/skin— mientras el
        // overlay está visible (electron#35030). El overlay (overlay.html) es solo un
        // banner de texto pasivo: no tiene zonas interactivas que necesiten recibir
        // mousemove/mouseleave, así que forward no aporta nada y solo causa el bug.
        // Mismo criterio que la skin window (ver updateSkinWindowInteractivity).
        overlayWindow.setIgnoreMouseEvents(shouldIgnoreMouse);
    } catch (err) {
        console.error(`[Overlay] No se pudo actualizar click-through: ${err.message}`);
    }

    if (typeof overlayWindow.setFocusable === 'function') {
        try {
            overlayWindow.setFocusable(!shouldIgnoreMouse);
        } catch (err) {
            console.warn(`[Overlay] No se pudo actualizar focusable: ${err.message}`);
        }
    }
}

function broadcastOverlayState() {
    if (!overlayWindow || overlayWindow.isDestroyed()) return;
    overlayWindow.webContents.send('overlay-state', getOverlayStateSnapshot());
}

function commitOverlayBounds(nextBounds, options = {}) {
    if (!overlayWindow || overlayWindow.isDestroyed()) return getOverlayStateSnapshot();

    const normalizedBounds = normalizeOverlayBounds(nextBounds, options.displayId ?? overlayRuntimeState?.displayId ?? null);
    const finalBounds = {
        x: normalizedBounds.x,
        y: normalizedBounds.y,
        width: normalizedBounds.width,
        height: normalizedBounds.height,
    };

    overlayRuntimeState = {
        ...(overlayRuntimeState || {}),
        interactive: !!overlayRuntimeState?.interactive,
        bounds: finalBounds,
        scale: normalizedBounds.scale,
        displayId: normalizedBounds.displayId,
    };

    overlayWindow.setBounds(finalBounds, Boolean(options.animate));
    scheduleOverlayStatePersist();
    broadcastOverlayState();
    return getOverlayStateSnapshot();
}

function setOverlayInteractive(nextInteractive, options = {}) {
    if (!overlayRuntimeState) {
        overlayRuntimeState = loadOverlayStateFromDisk();
    }

    overlayRuntimeState = {
        ...overlayRuntimeState,
        interactive: overlayInteractionLocked && !options.force ? false : !!nextInteractive,
    };

    updateOverlayWindowInteractivity();
    broadcastOverlayState();
    return getOverlayStateSnapshot();
}

function setOverlayInteractionLocked(locked) {
    overlayInteractionLocked = !!locked;
    if (overlayInteractionLocked && overlayRuntimeState?.interactive) {
        overlayRuntimeState = {
            ...overlayRuntimeState,
            interactive: false,
        };
    }
    updateOverlayWindowInteractivity();
    broadcastOverlayState();
}

function setOverlayCharacterRuntime(payload = {}) {
    overlayCharacterRuntime = sanitizeCharacterRuntimeUpdate(payload);
    if (overlayWindow && !overlayWindow.isDestroyed()) {
        overlayWindow.webContents.send('overlay-character-runtime', overlayCharacterRuntime);
    }
    if (skinWindow && !skinWindow.isDestroyed()) {
        skinWindow.webContents.send('overlay-character-runtime', overlayCharacterRuntime);
    }
    // La emoción es un evento ONE-SHOT, no estado persistente. Ya se envió arriba
    // una vez; ahora la quitamos del estado base (-> 'neutral') para que NI el
    // snapshot de broadcastOverlayState NI los siguientes updates de runtime
    // (visemas/mouth/status, que llegan muchas veces por segundo al hablar) la
    // reemitan. Si se reemitiera, el skin reaplicaría setEmotion en cada frame:
    // la intensidad quedaría clavada en 1 (sonrisa pegada, nunca decae a neutral)
    // y el gesto corporal se reiniciaría constantemente (convulsión). El skin VRM
    // ignora 'neutral' (no está en su lista EMOTIONS), así que reenviarlo es no-op.
    if (overlayCharacterRuntime.emotion && overlayCharacterRuntime.emotion !== 'neutral') {
        overlayCharacterRuntime = { ...overlayCharacterRuntime, emotion: 'neutral' };
    }
    broadcastOverlayState();
    return overlayCharacterRuntime;
}

// ── Skin Window — bounds/state helpers ──────────────────────

function getSkinStatePath() {
    return path.join(app.getPath('userData'), SKIN_STATE_FILENAME);
}

function getDefaultSkinBounds(display = null, scale = 1.0) {
    const targetDisplay = display || screen.getPrimaryDisplay();
    const workArea = getOverlayDisplayBounds(targetDisplay);
    const normalizedScale = clampNumber(toFiniteNumber(scale, 1.0), SKIN_MIN_SCALE, SKIN_MAX_SCALE);
    const width = Math.round(SKIN_BASE_SIZE.width * normalizedScale);
    const height = Math.round(SKIN_BASE_SIZE.height * normalizedScale);
    return {
        x: Math.round(workArea.x + workArea.width - width - 40),
        y: Math.round(workArea.y + workArea.height - height - 40),
        width,
        height,
    };
}

function normalizeSkinBounds(rawBounds, preferredDisplayId = null) {
    const preferredDisplay = preferredDisplayId !== null ? getDisplayById(preferredDisplayId) : null;
    const fallbackDisplay = preferredDisplay || screen.getPrimaryDisplay();
    const desiredWidth = toFiniteNumber(rawBounds?.width, SKIN_BASE_SIZE.width);
    const inferredScale = desiredWidth / SKIN_BASE_SIZE.width;
    const scale = clampNumber(toFiniteNumber(rawBounds?.scale, inferredScale), SKIN_MIN_SCALE, SKIN_MAX_SCALE);
    const width = Math.round(SKIN_BASE_SIZE.width * scale);
    const height = Math.round(SKIN_BASE_SIZE.height * scale);
    const rawX = Math.round(toFiniteNumber(rawBounds?.x, getDefaultSkinBounds(fallbackDisplay, scale).x));
    const rawY = Math.round(toFiniteNumber(rawBounds?.y, getDefaultSkinBounds(fallbackDisplay, scale).y));
    const provisionalBounds = { x: rawX, y: rawY, width, height };

    if (!hasVisibleIntersection(provisionalBounds)) {
        const resetBounds = getDefaultSkinBounds(fallbackDisplay, scale);
        return {
            ...resetBounds,
            scale,
            displayId: fallbackDisplay.id,
        };
    }

    const matchedDisplay = screen.getDisplayMatching(provisionalBounds) || fallbackDisplay;
    const workArea = getOverlayDisplayBounds(matchedDisplay);
    const clampedWidth = Math.min(width, workArea.width);
    const clampedHeight = Math.min(height, workArea.height);
    const finalScale = clampNumber(
        Math.min(clampedWidth / SKIN_BASE_SIZE.width, clampedHeight / SKIN_BASE_SIZE.height),
        SKIN_MIN_SCALE,
        SKIN_MAX_SCALE
    );
    const finalWidth = Math.round(SKIN_BASE_SIZE.width * finalScale);
    const finalHeight = Math.round(SKIN_BASE_SIZE.height * finalScale);
    const x = clampNumber(rawX, workArea.x, workArea.x + workArea.width - finalWidth);
    const y = clampNumber(rawY, workArea.y, workArea.y + workArea.height - finalHeight);

    return {
        x,
        y,
        width: finalWidth,
        height: finalHeight,
        scale: finalScale,
        displayId: matchedDisplay.id,
    };
}

function loadSkinStateFromDisk() {
    const fallbackBounds = getDefaultSkinBounds();
    const fallbackState = {
        interactive: false,
        scale: 1.0,
        displayId: screen.getPrimaryDisplay().id,
        bounds: fallbackBounds,
    };

    try {
        const statePath = getSkinStatePath();
        if (!fs.existsSync(statePath)) return fallbackState;
        const rawState = JSON.parse(fs.readFileSync(statePath, 'utf8'));
        const normalizedBounds = normalizeSkinBounds(rawState?.bounds || rawState, rawState?.displayId ?? null);
        return {
            interactive: false,
            scale: normalizedBounds.scale,
            displayId: normalizedBounds.displayId,
            bounds: {
                x: normalizedBounds.x,
                y: normalizedBounds.y,
                width: normalizedBounds.width,
                height: normalizedBounds.height,
            },
        };
    } catch (err) {
        console.warn(`[Skin] No se pudo cargar estado persistido: ${err.message}`);
        return fallbackState;
    }
}

function persistSkinStateToDisk() {
    if (!app.isReady() || !skinRuntimeState) return;

    try {
        fs.mkdirSync(app.getPath('userData'), { recursive: true });
        fs.writeFileSync(
            getSkinStatePath(),
            JSON.stringify({
                bounds: skinRuntimeState.bounds,
                scale: skinRuntimeState.scale,
                displayId: skinRuntimeState.displayId,
            }, null, 2),
            'utf8'
        );
    } catch (err) {
        console.error(`[Skin] No se pudo persistir estado: ${err.message}`);
    }
}

function scheduleSkinStatePersist() {
    clearTimeout(skinStateSaveTimer);
    skinStateSaveTimer = setTimeout(() => {
        persistSkinStateToDisk();
    }, SKIN_SAVE_DEBOUNCE_MS);
}

function updateSkinWindowInteractivity() {
    if (!skinWindow || skinWindow.isDestroyed()) return;

    const shouldIgnoreMouse = !skinRuntimeState?.interactive;

    try {
        // Sin { forward: true }: en Windows instala un hook global de mouse que hace
        // parpadear/saltar CUALQUIER ventana del sistema al arrastrarla, incluso con
        // esta ventana oculta (electron#35030, cerrado not-planned). El hover lo
        // detecta el cursor poller del main process, asi que forward es innecesario.
        skinWindow.setIgnoreMouseEvents(shouldIgnoreMouse);
    } catch (err) {
        console.error(`[Skin] No se pudo actualizar click-through: ${err.message}`);
    }
}

// ── Cursor poller: detecta hover desde main process (fallback robusto
//    para Windows donde setIgnoreMouseEvents forward es poco fiable) ──

const SKIN_CURSOR_POLL_MS = 150;
const SKIN_CURSOR_MARGIN_PX = 8;
let skinCursorPollTimer = null;
let skinVoiceActive = false;
let skinCursorOutsideCount = 0;
let lastSkinMoveAt = 0;

function startSkinCursorPoll() {
    if (skinCursorPollTimer) return;
    skinCursorOutsideCount = 0;
    skinCursorPollTimer = setInterval(() => {
        if (!skinWindow || skinWindow.isDestroyed() || !skinWindow.isVisible()) return;

        const p = screen.getCursorScreenPoint();
        const b = skinWindow.getBounds();
        const inside = p.x >= b.x - SKIN_CURSOR_MARGIN_PX
            && p.x <= b.x + b.width + SKIN_CURSOR_MARGIN_PX
            && p.y >= b.y - SKIN_CURSOR_MARGIN_PX
            && p.y <= b.y + b.height + SKIN_CURSOR_MARGIN_PX;

        if (inside && !skinRuntimeState?.interactive) {
            skinCursorOutsideCount = 0;
            setSkinInteractive(true);
        } else if (!inside && skinRuntimeState?.interactive) {
            if (skinChatSavedBounds || skinVoiceActive) return;
            if (Date.now() - lastSkinMoveAt < 500) return;
            skinCursorOutsideCount += 1;
            if (skinCursorOutsideCount >= 2) {
                setSkinInteractive(false);
                skinCursorOutsideCount = 0;
            }
        } else if (inside) {
            skinCursorOutsideCount = 0;
        }
    }, SKIN_CURSOR_POLL_MS);
}

function stopSkinCursorPoll() {
    if (skinCursorPollTimer) {
        clearInterval(skinCursorPollTimer);
        skinCursorPollTimer = null;
    }
    skinCursorOutsideCount = 0;
}

function getSkinStateSnapshot() {
    const fallbackBounds = getDefaultSkinBounds();
    const activeBounds = skinWindow && !skinWindow.isDestroyed()
        ? skinWindow.getBounds()
        : (skinRuntimeState?.bounds || fallbackBounds);

    return {
        mode: currentSkinMode,
        interactive: !!skinRuntimeState?.interactive,
        visible: !!(skinWindow && !skinWindow.isDestroyed() && skinWindow.isVisible()),
        displayId: skinRuntimeState?.displayId ?? null,
        scale: skinRuntimeState?.scale ?? 1.0,
        bounds: activeBounds,
        minScale: SKIN_MIN_SCALE,
        maxScale: SKIN_MAX_SCALE,
        baseWidth: SKIN_BASE_SIZE.width,
        baseHeight: SKIN_BASE_SIZE.height,
        characterConfig: characterPreferences,
        characterRuntime: overlayCharacterRuntime,
        skins: scanAvailableSkinsCached(),
    };
}

function broadcastSkinState() {
    if (!app.isReady()) return;
    const snapshot = getSkinStateSnapshot();
    if (skinWindow && !skinWindow.isDestroyed()) {
        skinWindow.webContents.send('skin-state', snapshot);
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('skin-state', snapshot);
    }
}

function commitSkinBounds(nextBounds, options = {}) {
    if (!skinWindow || skinWindow.isDestroyed()) return getSkinStateSnapshot();

    const normalizedBounds = normalizeSkinBounds(nextBounds, options.displayId ?? skinRuntimeState?.displayId ?? null);
    const finalBounds = {
        x: normalizedBounds.x,
        y: normalizedBounds.y,
        width: normalizedBounds.width,
        height: normalizedBounds.height,
    };

    skinRuntimeState = {
        ...(skinRuntimeState || {}),
        interactive: !!skinRuntimeState?.interactive,
        bounds: finalBounds,
        scale: normalizedBounds.scale,
        displayId: normalizedBounds.displayId,
    };

    skinWindow.setBounds(finalBounds, Boolean(options.animate));
    scheduleSkinStatePersist();
    broadcastSkinState();
    return getSkinStateSnapshot();
}

function setSkinInteractive(nextInteractive) {
    if (!skinRuntimeState) {
        skinRuntimeState = loadSkinStateFromDisk();
    }

    skinRuntimeState = {
        ...skinRuntimeState,
        interactive: !!nextInteractive,
    };

    updateSkinWindowInteractivity();
    broadcastSkinState();
    return getSkinStateSnapshot();
}

let cachedSkins = null;
let cachedSkinsAt = 0;
const SKINS_CACHE_TTL_MS = 2000;
const SPRITE_FRAME_NAMES = ['idle', 'talk', 'blink', 'blink_talk'];
const SKIN_NAME_INVALID_CHARS = /[<>:"/\\|?*\x00-\x1f]/g;

function scanAvailableSkinsCached() {
    const now = Date.now();
    if (cachedSkins && (now - cachedSkinsAt) < SKINS_CACHE_TTL_MS) return cachedSkins;
    cachedSkins = scanAvailableSkins();
    cachedSkinsAt = now;
    return cachedSkins;
}

function invalidateSkinsCache() {
    cachedSkins = null;
    cachedSkinsAt = 0;
}

// "Girl xsd" -> "girl-xsd" (gmini-skin:// usa el id como hostname: lowercase, sin espacios).
function slugifySkinId(name) {
    const base = String(name || '')
        .normalize('NFD')
        .replace(/[̀-ͯ]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
    return base || 'skin';
}

function dedupeSkinId(candidate, usedIds) {
    let id = candidate;
    let n = 2;
    while (usedIds.has(id)) {
        id = `${candidate}-${n}`;
        n += 1;
    }
    usedIds.add(id);
    return id;
}

// Escanea data/skins/<groupDir>/* — carpeta = personaje. Si no hay skin.json,
// auto-detecta: 3D -> primer .vrm/.glb/.gltf; 2D -> requiere idle.png y suma
// talk/blink/blink_talk + emociones (CHARACTER_EMOTIONS) si existen los PNG.
function scanSkinGroup(groupDir, group, usedIds) {
    const results = [];
    const groupPath = path.join(DATA_SKINS_DIR, groupDir);
    let entries;
    try {
        entries = fs.readdirSync(groupPath, { withFileTypes: true });
    } catch (err) {
        return results;
    }

    for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        const dir = `${groupDir}/${entry.name}`;
        const folderPath = path.join(groupPath, entry.name);

        let manifest = null;
        const manifestPath = path.join(folderPath, 'skin.json');
        if (fs.existsSync(manifestPath)) {
            try {
                manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
            } catch (err) {
                console.warn(`[Skin] Manifiesto invalido en ${dir}: ${err.message}`);
            }
        }

        let type = manifest?.type ? String(manifest.type) : null;
        let resolved = manifest ? { ...manifest } : null;

        if (resolved) {
            if (group === '3d') {
                if (!resolved.model || !fs.existsSync(path.join(folderPath, String(resolved.model)))) continue;
                if (!type) type = 'glb';
            } else {
                if (!resolved.sprites?.idle || !fs.existsSync(path.join(folderPath, String(resolved.sprites.idle)))) continue;
                if (!type) type = 'sprite2d';
            }
        } else {
            let files;
            try {
                files = fs.readdirSync(folderPath, { withFileTypes: true })
                    .filter((f) => f.isFile())
                    .map((f) => f.name);
            } catch (err) {
                continue;
            }

            if (group === '3d') {
                const vrmFile = files.find((f) => /\.vrm$/i.test(f));
                const glbFile = files.find((f) => /\.(glb|gltf)$/i.test(f));
                if (vrmFile) {
                    type = 'vrm';
                    resolved = { name: entry.name, model: vrmFile };
                } else if (glbFile) {
                    type = 'glb';
                    resolved = { name: entry.name, model: glbFile };
                } else {
                    continue;
                }
            } else {
                const lowerMap = new Map(files.map((f) => [f.toLowerCase(), f]));
                if (!lowerMap.has('idle.png')) continue;
                const sprites = {};
                for (const frame of SPRITE_FRAME_NAMES) {
                    const fname = `${frame}.png`;
                    if (lowerMap.has(fname)) sprites[frame] = lowerMap.get(fname);
                }
                const emotions = {};
                for (const emo of CHARACTER_EMOTIONS) {
                    if (emo === 'neutral') continue;
                    const fname = `${emo}.png`;
                    if (lowerMap.has(fname)) emotions[emo] = lowerMap.get(fname);
                }
                if (Object.keys(emotions).length) sprites.emotions = emotions;
                type = 'sprite2d';
                resolved = { name: entry.name, sprites };
            }
        }

        const id = dedupeSkinId(slugifySkinId(resolved.id || entry.name), usedIds);
        resolved.id = id;
        resolved.name = String(resolved.name || entry.name);
        resolved.type = type;

        results.push({ id, name: resolved.name, type, group, dir, manifest: resolved });
    }

    return results;
}

function scanAvailableSkins() {
    const usedIds = new Set(['energy-ball']);
    const skins = [
        {
            id: 'energy-ball',
            name: 'Bola de energia',
            type: 'procedural',
            group: '3d',
            dir: null,
            manifest: { id: 'energy-ball', name: 'Bola de energia' },
        },
    ];

    try {
        skins.push(...scanSkinGroup('3D', '3d', usedIds));
        skins.push(...scanSkinGroup('2D', '2d', usedIds));
    } catch (err) {
        console.warn(`[Skin] No se pudo escanear skins: ${err.message}`);
    }

    return skins;
}

// Crea data/skins/<3D|2D>/<Nombre>/ a partir de archivos elegidos via skin:pick-file.
// 3D requiere un modelo .vrm/.glb/.gltf; 2D requiere los 4 sprites base
// (idle/talk/blink/blink_talk); emociones (CHARACTER_EMOTIONS) son opcionales.
function createSkin(payload = {}) {
    const group = payload.group === '2d' ? '2d' : payload.group === '3d' ? '3d' : null;
    if (!group) return { ok: false, error: 'invalid-group' };

    const name = String(payload.name || '')
        .trim()
        .replace(SKIN_NAME_INVALID_CHARS, '')
        .replace(/[.\s]+$/, '');
    if (!name) return { ok: false, error: 'invalid-name' };

    const id = slugifySkinId(name);
    if (scanAvailableSkins().some((s) => s.id === id)) {
        return { ok: false, error: 'duplicate' };
    }

    const groupDir = group === '3d' ? '3D' : '2D';
    const targetDir = path.join(DATA_SKINS_DIR, groupDir, name);
    if (fs.existsSync(targetDir)) {
        return { ok: false, error: 'duplicate' };
    }

    let manifestObj;
    try {
        if (group === '3d') {
            const modelSrc = String(payload.model || '');
            const ext = path.extname(modelSrc).toLowerCase();
            if (!modelSrc || !fs.existsSync(modelSrc) || !['.vrm', '.glb', '.gltf'].includes(ext)) {
                return { ok: false, error: 'missing-model' };
            }
            fs.mkdirSync(targetDir, { recursive: true });
            const modelFile = `model${ext}`;
            fs.copyFileSync(modelSrc, path.join(targetDir, modelFile));
            manifestObj = { id, name, type: ext === '.vrm' ? 'vrm' : 'glb', model: modelFile };
        } else {
            const sprites = payload.sprites || {};
            for (const frame of SPRITE_FRAME_NAMES) {
                const src = String(sprites[frame] || '');
                if (!src || !fs.existsSync(src)) {
                    return { ok: false, error: 'missing-sprites' };
                }
            }
            fs.mkdirSync(targetDir, { recursive: true });
            const spritesOut = {};
            for (const frame of SPRITE_FRAME_NAMES) {
                const fname = `${frame}.png`;
                fs.copyFileSync(String(sprites[frame]), path.join(targetDir, fname));
                spritesOut[frame] = fname;
            }
            const emotionsOut = {};
            const emotions = sprites.emotions || {};
            for (const emo of CHARACTER_EMOTIONS) {
                if (emo === 'neutral') continue;
                const src = String(emotions[emo] || '');
                if (!src || !fs.existsSync(src)) continue;
                const fname = `${emo}.png`;
                fs.copyFileSync(src, path.join(targetDir, fname));
                emotionsOut[emo] = fname;
            }
            if (Object.keys(emotionsOut).length) spritesOut.emotions = emotionsOut;
            manifestObj = { id, name, type: 'sprite2d', sprites: spritesOut };
        }

        fs.writeFileSync(path.join(targetDir, 'skin.json'), JSON.stringify(manifestObj, null, 2));
    } catch (err) {
        try {
            fs.rmSync(targetDir, { recursive: true, force: true });
        } catch (cleanupErr) {
            // ignore
        }
        return { ok: false, error: 'copy-failed', detail: err.message };
    }

    invalidateSkinsCache();
    broadcastSkinState();
    return { ok: true, skin: { id, name, type: manifestObj.type, group, dir: `${groupDir}/${name}` } };
}

// ── Skin Mode (avatar flotante vs ventana de chat) ──────────

function setSkinMode(mode) {
    const nextMode = mode === 'skin' ? 'skin' : 'chat';
    currentSkinMode = nextMode;

    if (nextMode === 'skin') {
        if (!skinWindow || skinWindow.isDestroyed()) {
            createSkinWindow();
        }
        if (skinWindow) {
            if (typeof skinWindow.showInactive === 'function') {
                skinWindow.showInactive();
            } else {
                skinWindow.show();
            }
            setSkinInteractive(false);
            broadcastSkinState();
            startSkinCursorPoll();
        }
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.hide();
        }
    } else {
        stopSkinCursorPoll();
        if (skinWindow && !skinWindow.isDestroyed()) {
            setSkinInteractive(false);
            if (skinChatSavedBounds) {
                skinWindow.setBounds(skinChatSavedBounds);
                skinChatSavedBounds = null;
            }
            skinWindow.hide();
            broadcastSkinState();
        }
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.show();
            mainWindow.focus();
        }
    }

    refreshTrayMenu();
    return getSkinStateSnapshot();
}

function focusPrimaryWindow() {
    if (mainWindow && !mainWindow.isDestroyed()) {
        if (mainWindow.isMinimized()) {
            mainWindow.restore();
        }
        if (!mainWindow.isVisible()) {
            mainWindow.show();
        }
        mainWindow.focus();
        return;
    }

    if (!app.isReady()) return;

    createMainWindow();
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
        mainWindow.focus();
    }
}

function isExpectedBackendHealthPayload(payload) {
    return !!payload
        && payload.status === 'ok'
        && typeof payload.version === 'string'
        && Number.isFinite(Number(payload.uptime_seconds));
}

async function getBackendHealthStatus() {
    let timeoutId = null;
    try {
        const controller = new AbortController();
        timeoutId = setTimeout(() => controller.abort(), BACKEND_HEALTH_REQUEST_TIMEOUT_MS);
        const response = await fetch(BACKEND_HEALTH_URL, {
            method: 'GET',
            cache: 'no-store',
            signal: controller.signal,
        });

        if (!response.ok) {
            return { ok: false, statusCode: response.status };
        }

        const payload = await response.json();
        if (!isExpectedBackendHealthPayload(payload)) {
            return { ok: false, statusCode: response.status, payload };
        }

        return { ok: true, statusCode: response.status, payload };
    } catch (err) {
        return { ok: false, error: err };
    } finally {
        if (timeoutId) clearTimeout(timeoutId);
    }
}

// ── Backend Process Management ───────────────────────────────

async function startBackend() {
    if (backendProcess && backendProcessOwnership === 'owned') {
        return true;
    }

    const existingBackend = await getBackendHealthStatus();
    if (existingBackend.ok) {
        backendProcess = null;
        backendProcessOwnership = 'reused';
        console.log(`[Backend] Reutilizando backend ya activo en ${BACKEND_URL}`);
        return true;
    }

    return new Promise((resolve) => {
        const venvPython = path.join(PROJECT_ROOT, 'venv', 'Scripts', 'python.exe');
        const fallbackPython = 'python';

        // Intentar con el venv primero, fallback a python global
        const pythonPath = fs.existsSync(venvPython) ? venvPython : fallbackPython;

        console.log(`[Backend] Iniciando con: ${pythonPath}`);

        const child = spawn(pythonPath, ['-m', 'backend.main'], {
            cwd: PROJECT_ROOT,
            env: {
                ...process.env,
                PYTHONUNBUFFERED: '1',
                PYTHONIOENCODING: 'utf-8',
                PYTHONUTF8: '1',
            },
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        backendProcess = child;
        backendProcessOwnership = 'owned';

        let started = false;
        let settled = false;
        let healthCheckInFlight = false;
        let healthPollTimer = null;
        let timeoutHandle = null;

        const finish = (ok) => {
            if (settled) return;
            settled = true;
            if (healthPollTimer) clearInterval(healthPollTimer);
            if (timeoutHandle) clearTimeout(timeoutHandle);
            resolve(ok);
        };

        const probeHealth = async () => {
            if (settled || started || backendProcess !== child || healthCheckInFlight) return;
            healthCheckInFlight = true;
            try {
                const health = await getBackendHealthStatus();
                if (health.ok && !started) {
                    started = true;
                    finish(true);
                }
            } finally {
                healthCheckInFlight = false;
            }
        };

        child.stdout.on('data', (data) => {
            const text = data.toString('utf8');
            process.stdout.write(`[Backend] ${text}`);
        });

        child.stderr.on('data', (data) => {
            process.stderr.write(`[Backend] ${data.toString('utf8')}`);
        });

        child.on('error', (err) => {
            console.error(`[Backend] Error al iniciar: ${err.message}`);
            if (backendProcess === child) {
                backendProcess = null;
                backendProcessOwnership = 'none';
            }
            if (!started) finish(false);
        });

        child.on('exit', (code) => {
            console.log(`[Backend] Proceso termino con codigo: ${code}`);
            if (backendProcess === child) {
                backendProcess = null;
                backendProcessOwnership = 'none';
            }
            if (!started) finish(false);
        });

        healthPollTimer = setInterval(() => {
            void probeHealth();
        }, BACKEND_HEALTH_CHECK_INTERVAL_MS);
        void probeHealth();

        // Timeout: si no arranca en 60s, continuar igual
        timeoutHandle = setTimeout(() => {
            if (!started) {
                console.warn('[Backend] Timeout esperando arranque, continuando...');
                finish(false);
            }
        }, BACKEND_START_TIMEOUT_MS);
    });
}

function stopBackend() {
    if (backendProcessOwnership === 'reused') {
        console.log('[Backend] Backend reutilizado; esta instancia no lo detiene.');
        backendProcessOwnership = 'none';
        return;
    }

    if (backendProcess && backendProcessOwnership === 'owned') {
        const child = backendProcess;
        console.log('[Backend] Deteniendo...');
        backendProcessOwnership = 'none';
        child.kill('SIGTERM');
        // Forzar kill si no termina en 3s
        setTimeout(() => {
            if (backendProcess === child) {
                child.kill('SIGKILL');
                backendProcess = null;
            }
        }, 3000);
    }
}

// ── Main Window ──────────────────────────────────────────────

function createMainWindow() {
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;

    mainWindow = new BrowserWindow({
        width: 420,
        height: 700,
        minWidth: 360,
        minHeight: 500,
        x: width - 440,
        y: height - 720,
        frame: false,
        transparent: false,
        resizable: true,
        alwaysOnTop: true,
        skipTaskbar: false,
        show: false,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            backgroundThrottling: false,
        },
        // No hay .ico en assets; Electron acepta PNG como icono de ventana en Windows.
        icon: path.join(__dirname, 'assets', 'icon.png'),
        title: 'G-Mini Agent',
    });

    mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));

    mainWindow.once('ready-to-show', () => {
        if (!appPreferences.startHiddenToTray && currentSkinMode !== 'skin') {
            mainWindow.show();
            mainWindow.focus();
        }
    });

    mainWindow.on('minimize', (e) => {
        if (appPreferences.minimizeToTray && !app.isQuitting) {
            e.preventDefault();
            mainWindow.hide();
        }
    });

    mainWindow.on('close', (e) => {
        if (!app.isQuitting && appPreferences.closeToTray) {
            e.preventDefault();
            mainWindow.hide();
        }
    });

    mainWindow.on('closed', () => {
        mainWindow = null;
    });

    // DevTools en modo dev
    if (process.argv.includes('--dev')) {
        mainWindow.webContents.openDevTools({ mode: 'detach' });
    }
}

// ── Overlay Window (transparente, click-through) ────────────

function createOverlayWindow() {
    overlayRuntimeState = loadOverlayStateFromDisk();
    const overlayBounds = overlayRuntimeState.bounds || getDefaultOverlayBounds();

    overlayWindow = new BrowserWindow({
        width: overlayBounds.width,
        height: overlayBounds.height,
        x: overlayBounds.x,
        y: overlayBounds.y,
        frame: false,
        transparent: true,
        alwaysOnTop: true,
        skipTaskbar: true,
        resizable: false,
        focusable: true,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    overlayWindow.loadFile(path.join(__dirname, 'src', 'overlay.html'));
    overlayWindow.setVisibleOnAllWorkspaces(true);
    overlayWindow.hide();
    updateOverlayWindowInteractivity();

    overlayWindow.webContents.on('did-finish-load', () => {
        overlayWindow.webContents.send('overlay-text', lastOverlayText);
        broadcastOverlayState();
    });

    overlayWindow.on('move', () => {
        if (overlayWindow.isDestroyed()) return;
        const bounds = overlayWindow.getBounds();
        const normalizedBounds = normalizeOverlayBounds(bounds, overlayRuntimeState?.displayId ?? null);
        overlayRuntimeState = {
            ...(overlayRuntimeState || {}),
            interactive: !!overlayRuntimeState?.interactive,
            scale: normalizedBounds.scale,
            displayId: normalizedBounds.displayId,
            bounds: {
                x: bounds.x,
                y: bounds.y,
                width: bounds.width,
                height: bounds.height,
            },
        };
        scheduleOverlayStatePersist();
        broadcastOverlayState();
    });

    overlayWindow.on('resize', () => {
        if (overlayWindow.isDestroyed()) return;
        const bounds = overlayWindow.getBounds();
        const normalizedBounds = normalizeOverlayBounds(bounds, overlayRuntimeState?.displayId ?? null);
        overlayRuntimeState = {
            ...(overlayRuntimeState || {}),
            interactive: !!overlayRuntimeState?.interactive,
            scale: normalizedBounds.scale,
            displayId: normalizedBounds.displayId,
            bounds: {
                x: bounds.x,
                y: bounds.y,
                width: bounds.width,
                height: bounds.height,
            },
        };
        scheduleOverlayStatePersist();
        broadcastOverlayState();
    });

    overlayWindow.on('closed', () => {
        overlayWindow = null;
    });
}

// ── Skin Window (avatar 2D/3D transparente, click-through) ──

function createSkinWindow() {
    skinRuntimeState = loadSkinStateFromDisk();
    const skinBounds = skinRuntimeState.bounds || getDefaultSkinBounds();

    skinWindow = new BrowserWindow({
        width: skinBounds.width,
        height: skinBounds.height,
        x: skinBounds.x,
        y: skinBounds.y,
        frame: false,
        transparent: true,
        alwaysOnTop: true,
        skipTaskbar: true,
        resizable: false,
        focusable: true,
        hasShadow: false,
        show: false,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            backgroundThrottling: false,
        },
    });

    skinWindow.loadFile(path.join(__dirname, 'src', 'skin.html'));
    skinWindow.setVisibleOnAllWorkspaces(true);
    updateSkinWindowInteractivity();

    skinWindow.webContents.on('did-finish-load', () => {
        broadcastSkinState();
        skinWindow.webContents.send('overlay-character-runtime', overlayCharacterRuntime);
    });

    let skinMoveBroadcastTimer = null;
    skinWindow.on('move', () => {
        if (skinWindow.isDestroyed()) return;
        if (skinChatSavedBounds) return;
        const bounds = skinWindow.getBounds();
        const normalizedBounds = normalizeSkinBounds(bounds, skinRuntimeState?.displayId ?? null);
        skinRuntimeState = {
            ...(skinRuntimeState || {}),
            interactive: !!skinRuntimeState?.interactive,
            scale: normalizedBounds.scale,
            displayId: normalizedBounds.displayId,
            bounds: {
                x: bounds.x,
                y: bounds.y,
                width: bounds.width,
                height: bounds.height,
            },
        };
        scheduleSkinStatePersist();
        if (skinMoveBroadcastTimer) clearTimeout(skinMoveBroadcastTimer);
        skinMoveBroadcastTimer = setTimeout(() => {
            skinMoveBroadcastTimer = null;
            broadcastSkinState();
        }, 100);
    });

    skinWindow.on('closed', () => {
        stopSkinCursorPoll();
        skinWindow = null;
    });
}

// ── Tray ─────────────────────────────────────────────────────

function createTray() {
    const { nativeImage } = require('electron');
    // Fallback a icon.png si por algún motivo falta el tray-icon dedicado.
    const fs = require('fs');
    let iconPath = path.join(__dirname, 'assets', 'tray-icon.png');
    if (!fs.existsSync(iconPath)) {
        iconPath = path.join(__dirname, 'assets', 'icon.png');
    }
    let icon;

    try {
        icon = nativeImage.createFromPath(iconPath);
        // El arte fuente es 551x551. La bandeja de Windows muestra 16px (24 a 150%,
        // 32 a 200%). Damos 32x32: el SO lo reduce limpio a cualquier slot/DPI
        // (reducir es nítido; ampliar desde 16 se ve borroso). 'best' = mejor filtro.
        if (!icon.isEmpty()) {
            icon = icon.resize({ width: 32, height: 32, quality: 'best' });
        }
    } catch (e) {
        icon = nativeImage.createEmpty();
    }

    tray = new Tray(icon);

    tray.setToolTip('G-Mini Agent');
    refreshTrayMenu();

    tray.on('click', () => {
        if (currentSkinMode === 'skin') {
            if (!skinWindow || skinWindow.isDestroyed()) {
                createSkinWindow();
            }
            if (skinWindow) {
                if (typeof skinWindow.showInactive === 'function') skinWindow.showInactive();
                else skinWindow.show();
                broadcastSkinState();
                startSkinCursorPoll();
            }
            refreshTrayMenu();
            return;
        }
        if (mainWindow) {
            if (mainWindow.isVisible()) {
                mainWindow.focus();
            } else {
                mainWindow.show();
            }
        }
    });
}

// ── Overlay Toggle ───────────────────────────────────────────

function toggleOverlay(enable) {
    isOverlayMode = enable;
    if (!overlayWindow || overlayWindow.isDestroyed()) {
        createOverlayWindow();
    }
    if (enable) {
        if (overlayWindow) {
            setOverlayInteractive(false, { force: true });
            if (typeof overlayWindow.showInactive === 'function') {
                overlayWindow.showInactive();
            } else {
                overlayWindow.show();
            }
            // Reaplicar con la ventana ya visible para que forward se active.
            updateOverlayWindowInteractivity();
            broadcastOverlayState();
        }
    } else {
        if (overlayWindow) {
            setOverlayInteractive(false, { force: true });
            overlayWindow.hide();
            // Reaplicar con la ventana oculta para desinstalar el hook de forward.
            updateOverlayWindowInteractivity();
        }
    }
    refreshTrayMenu();
}

// ── IPC Handlers ─────────────────────────────────────────────

ipcMain.handle('get-backend-url', () => BACKEND_URL);

// ── Guardar media generada (imagen/video/audio) en una carpeta a eleccion ──
function _fetchBufferFromUrl(url) {
    return new Promise((resolve, reject) => {
        let lib;
        try {
            lib = url.startsWith('https:') ? require('https') : require('http');
        } catch (e) {
            reject(e);
            return;
        }
        const req = lib.get(url, (res) => {
            if (res.statusCode !== 200) {
                res.resume();
                reject(new Error('HTTP ' + res.statusCode));
                return;
            }
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => resolve(Buffer.concat(chunks)));
        });
        req.on('error', reject);
        req.setTimeout(30000, () => req.destroy(new Error('timeout')));
    });
}

ipcMain.handle('save-media-as', async (_, url, filename) => {
    try {
        if (!url) return { ok: false, error: 'sin url' };

        const suggested = filename || (() => {
            try { return decodeURIComponent(url.split('/').pop().split('?')[0]) || 'archivo'; }
            catch (e) { return 'archivo'; }
        })();

        const result = await dialog.showSaveDialog(mainWindow, {
            title: 'Guardar archivo generado',
            defaultPath: suggested,
        });
        if (result.canceled || !result.filePath) return { ok: false, canceled: true };

        let buf;
        if (url.startsWith('data:')) {
            const comma = url.indexOf(',');
            const meta = url.slice(5, comma);
            const dataPart = url.slice(comma + 1);
            buf = meta.includes('base64')
                ? Buffer.from(dataPart, 'base64')
                : Buffer.from(decodeURIComponent(dataPart), 'utf8');
        } else {
            buf = await _fetchBufferFromUrl(url);
        }

        fs.writeFileSync(result.filePath, buf);
        return { ok: true, path: result.filePath };
    } catch (e) {
        return { ok: false, error: String((e && e.message) || e) };
    }
});

ipcMain.handle('minimize-window', () => {
    if (mainWindow) mainWindow.minimize();
});

ipcMain.handle('close-window', () => {
    if (mainWindow) mainWindow.close();
});

ipcMain.handle('toggle-always-on-top', (_, value) => {
    if (mainWindow) mainWindow.setAlwaysOnTop(value);
});

ipcMain.handle('toggle-overlay', (_, enable) => {
    toggleOverlay(enable);
    return getOverlayStateSnapshot();
});

ipcMain.handle('set-overlay-text', (_, text) => {
    lastOverlayText = String(text || '');
    if (overlayWindow) {
        overlayWindow.webContents.send('overlay-text', lastOverlayText);
    }
    return { success: true };
});

ipcMain.handle('overlay:get-state', () => {
    return getOverlayStateSnapshot();
});

ipcMain.handle('overlay:set-interactive', (_, interactive) => {
    return setOverlayInteractive(interactive);
});

ipcMain.handle('overlay:set-character-runtime', (_, payload = {}) => {
    return setOverlayCharacterRuntime(payload);
});

ipcMain.handle('overlay:move-by', (_, dx, dy) => {
    if (!overlayWindow || overlayWindow.isDestroyed()) {
        return getOverlayStateSnapshot();
    }
    const bounds = overlayWindow.getBounds();
    return commitOverlayBounds({
        x: Math.round(bounds.x + toFiniteNumber(dx, 0)),
        y: Math.round(bounds.y + toFiniteNumber(dy, 0)),
        width: bounds.width,
        height: bounds.height,
    });
});

ipcMain.handle('overlay:resize-by', (_, delta, anchor = 'center') => {
    if (!overlayWindow || overlayWindow.isDestroyed()) {
        return getOverlayStateSnapshot();
    }

    const currentBounds = overlayWindow.getBounds();
    const currentScale = clampNumber(
        toFiniteNumber(overlayRuntimeState?.scale, currentBounds.width / OVERLAY_BASE_SIZE.width),
        OVERLAY_MIN_SCALE,
        OVERLAY_MAX_SCALE
    );
    const nextScale = clampNumber(currentScale + toFiniteNumber(delta, 0), OVERLAY_MIN_SCALE, OVERLAY_MAX_SCALE);
    const nextWidth = Math.round(OVERLAY_BASE_SIZE.width * nextScale);
    const nextHeight = Math.round(OVERLAY_BASE_SIZE.height * nextScale);
    const widthDelta = nextWidth - currentBounds.width;
    const heightDelta = nextHeight - currentBounds.height;

    let nextX = currentBounds.x;
    let nextY = currentBounds.y;

    if (anchor === 'top-left') {
        nextX = currentBounds.x;
        nextY = currentBounds.y;
    } else if (anchor === 'bottom-right') {
        nextX = currentBounds.x - widthDelta;
        nextY = currentBounds.y - heightDelta;
    } else {
        nextX = currentBounds.x - Math.round(widthDelta / 2);
        nextY = currentBounds.y - Math.round(heightDelta / 2);
    }

    return commitOverlayBounds({
        x: nextX,
        y: nextY,
        width: nextWidth,
        height: nextHeight,
    });
});

ipcMain.handle('overlay:set-bounds', (_, rawBounds = {}) => {
    if (!overlayWindow || overlayWindow.isDestroyed()) {
        return getOverlayStateSnapshot();
    }
    return commitOverlayBounds(rawBounds);
});

// ── Skin IPC ─────────────────────────────────────────────────

ipcMain.handle('skin:set-mode', (_, mode) => {
    return setSkinMode(mode);
});

ipcMain.handle('skin:get-state', () => {
    return getSkinStateSnapshot();
});

ipcMain.handle('skin:list', () => {
    return scanAvailableSkins();
});

ipcMain.handle('skin:pick-file', async (_, kind) => {
    if (!mainWindow || mainWindow.isDestroyed()) return null;
    const filters = kind === 'model'
        ? [{ name: 'Modelo 3D (VRoid)', extensions: ['vrm', 'glb', 'gltf'] }]
        : [{ name: 'Imagen PNG', extensions: ['png'] }];
    const result = await dialog.showOpenDialog(mainWindow, {
        title: kind === 'model' ? 'Selecciona el modelo .vrm o .glb' : 'Selecciona la imagen PNG',
        properties: ['openFile'],
        filters,
    });
    if (result.canceled || !result.filePaths.length) return null;
    return result.filePaths[0];
});

// Adjuntos del chat: archivos (multi) o una carpeta. Devuelve array de rutas.
ipcMain.handle('pick-attachments', async (_, mode = 'files') => {
    if (!mainWindow || mainWindow.isDestroyed()) return [];
    const isFolder = mode === 'folder';
    const result = await dialog.showOpenDialog(mainWindow, {
        title: isFolder ? 'Selecciona una carpeta' : 'Selecciona archivos para adjuntar',
        properties: isFolder
            ? ['openDirectory']
            : ['openFile', 'multiSelections'],
    });
    if (result.canceled || !result.filePaths.length) return [];
    return result.filePaths;
});

ipcMain.handle('skin:create', (_, payload = {}) => {
    return createSkin(payload || {});
});

ipcMain.handle('skin:set-interactive', (_, interactive) => {
    return setSkinInteractive(interactive);
});

ipcMain.handle('skin:move-by', (_, dx, dy) => {
    if (!skinWindow || skinWindow.isDestroyed()) {
        return getSkinStateSnapshot();
    }
    lastSkinMoveAt = Date.now();
    const bounds = skinWindow.getBounds();
    return commitSkinBounds({
        x: Math.round(bounds.x + toFiniteNumber(dx, 0)),
        y: Math.round(bounds.y + toFiniteNumber(dy, 0)),
        width: bounds.width,
        height: bounds.height,
    });
});

ipcMain.handle('skin:resize-by', (_, delta) => {
    if (!skinWindow || skinWindow.isDestroyed()) {
        return getSkinStateSnapshot();
    }

    const currentBounds = skinWindow.getBounds();
    const currentScale = clampNumber(
        toFiniteNumber(skinRuntimeState?.scale, currentBounds.width / SKIN_BASE_SIZE.width),
        SKIN_MIN_SCALE,
        SKIN_MAX_SCALE
    );
    const nextScale = clampNumber(currentScale + toFiniteNumber(delta, 0), SKIN_MIN_SCALE, SKIN_MAX_SCALE);
    const nextWidth = Math.round(SKIN_BASE_SIZE.width * nextScale);
    const nextHeight = Math.round(SKIN_BASE_SIZE.height * nextScale);
    const widthDelta = nextWidth - currentBounds.width;
    const heightDelta = nextHeight - currentBounds.height;

    // Ancla abajo-centro: los "pies" del personaje quedan fijos al redimensionar.
    const nextX = currentBounds.x - Math.round(widthDelta / 2);
    const nextY = currentBounds.y - heightDelta;

    return commitSkinBounds({
        x: nextX,
        y: nextY,
        width: nextWidth,
        height: nextHeight,
    });
});

ipcMain.handle('skin:set-bounds', (_, rawBounds = {}) => {
    if (!skinWindow || skinWindow.isDestroyed()) {
        return getSkinStateSnapshot();
    }
    return commitSkinBounds(rawBounds);
});

ipcMain.handle('skin:minimize', () => {
    stopSkinCursorPoll();
    if (skinWindow && !skinWindow.isDestroyed()) {
        setSkinInteractive(false);
        if (skinChatSavedBounds) {
            skinWindow.setBounds(skinChatSavedBounds);
            skinChatSavedBounds = null;
        }
        skinWindow.hide();
        broadcastSkinState();
    }
    refreshTrayMenu();
    return getSkinStateSnapshot();
});

// ── Skin: mini-chat burbuja (proxy IPC, sin segundo socket) ────

ipcMain.handle('skin:chat-open', () => {
    if (!skinWindow || skinWindow.isDestroyed()) return null;
    const bounds = skinWindow.getBounds();
    if (!skinChatSavedBounds) {
        skinChatSavedBounds = { ...bounds };
        skinWindow.setBounds({ ...bounds, width: bounds.width + SKIN_CHAT_BUBBLE_WIDTH });
    }
    return skinChatSavedBounds;
});

ipcMain.handle('skin:chat-close', () => {
    if (skinWindow && !skinWindow.isDestroyed() && skinChatSavedBounds) {
        skinWindow.setBounds(skinChatSavedBounds);
    }
    skinChatSavedBounds = null;
    return getSkinStateSnapshot();
});

ipcMain.handle('skin:chat-send', (_, text) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('skin-chat-send', String(text || ''));
    }
    return true;
});

ipcMain.handle('skin:chat-relay', (_, payload) => {
    if (skinWindow && !skinWindow.isDestroyed()) {
        skinWindow.webContents.send('skin-chat-relay', payload);
    }
    return true;
});

// ── Skin: boton mic (proxy hacia voz en tiempo real de mainWindow) ────

ipcMain.handle('skin:voice-toggle', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('skin-voice-toggle');
    }
    return true;
});

ipcMain.handle('skin:voice-state', (_, payload) => {
    skinVoiceActive = !!payload?.active;
    if (skinWindow && !skinWindow.isDestroyed()) {
        skinWindow.webContents.send('skin-voice-state', payload);
    }
    return true;
});

ipcMain.handle('get-app-runtime-settings', () => {
    return getEffectiveAppRuntimeSettings();
});

ipcMain.handle('reload-app-runtime-settings', () => {
    applyAppPreferences(loadAppPreferencesFromDisk());
    return getEffectiveAppRuntimeSettings();
});

// ── Effect Handlers (Click indicators, Screenshot overlay, Transparency) ────

let actionOverlayWindow = null;

function createActionOverlayWindow() {
    const { width, height } = screen.getPrimaryDisplay().size;

    actionOverlayWindow = new BrowserWindow({
        width: width,
        height: height,
        x: 0,
        y: 0,
        frame: false,
        transparent: true,
        alwaysOnTop: true,
        skipTaskbar: true,
        resizable: false,
        focusable: false,
        hasShadow: false,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    // Cargar HTML inline para el overlay de acciones
    actionOverlayWindow.loadURL(`data:text/html,
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                background: transparent; 
                overflow: hidden;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }

            /* ── Click Indicator ── */
            .click-point {
                position: fixed;
                pointer-events: none;
                z-index: 10000;
                transform: translate(-50%, -50%);
            }
            .click-point .dot {
                width: 16px;
                height: 16px;
                border-radius: 50%;
                background: #6366f1;
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                box-shadow: 0 0 12px rgba(99, 102, 241, 0.8), 0 0 24px rgba(99, 102, 241, 0.4);
                animation: dot-pop 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
            }
            .click-point .ripple {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%) scale(0);
                border-radius: 50%;
                border: 2.5px solid rgba(99, 102, 241, 0.7);
                pointer-events: none;
            }
            .click-point .ripple-1 {
                width: 50px;
                height: 50px;
                animation: ripple-out 0.7s cubic-bezier(0.25, 0.46, 0.45, 0.94) forwards;
            }
            .click-point .ripple-2 {
                width: 80px;
                height: 80px;
                animation: ripple-out 0.7s cubic-bezier(0.25, 0.46, 0.45, 0.94) 0.1s forwards;
            }
            .click-point .ripple-3 {
                width: 120px;
                height: 120px;
                animation: ripple-out 0.7s cubic-bezier(0.25, 0.46, 0.45, 0.94) 0.2s forwards;
            }
            .click-point .coord-label {
                position: absolute;
                top: -32px;
                left: 50%;
                transform: translateX(-50%);
                background: rgba(30, 30, 40, 0.85);
                color: #a5b4fc;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 10px;
                border-radius: 6px;
                white-space: nowrap;
                backdrop-filter: blur(6px);
                border: 1px solid rgba(99, 102, 241, 0.3);
                animation: label-fade 0.8s ease forwards;
                letter-spacing: 0.5px;
            }

            /* Double click — second ring */
            .click-point.double_click .ripple-1 { animation-iteration-count: 2; }
            .click-point.double_click .dot { background: #818cf8; }

            /* Right click — orange tint */
            .click-point.right_click .dot { background: #f59e0b; box-shadow: 0 0 12px rgba(245, 158, 11, 0.8); }
            .click-point.right_click .ripple { border-color: rgba(245, 158, 11, 0.6); }
            .click-point.right_click .coord-label { color: #fcd34d; border-color: rgba(245, 158, 11, 0.3); }

            @keyframes dot-pop {
                0% { transform: translate(-50%, -50%) scale(0); opacity: 1; }
                40% { transform: translate(-50%, -50%) scale(1.4); opacity: 1; }
                100% { transform: translate(-50%, -50%) scale(0); opacity: 0; }
            }
            @keyframes ripple-out {
                0% { transform: translate(-50%, -50%) scale(0); opacity: 0.8; }
                100% { transform: translate(-50%, -50%) scale(1); opacity: 0; }
            }
            @keyframes label-fade {
                0% { opacity: 0; transform: translateX(-50%) translateY(6px); }
                20% { opacity: 1; transform: translateX(-50%) translateY(0); }
                70% { opacity: 1; }
                100% { opacity: 0; }
            }

            /* ── Screenshot Overlay (phone-style) ── */
            #screenshot-overlay {
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                pointer-events: none;
                z-index: 9999;
                display: none;
            }
            #screenshot-overlay.active {
                display: block;
            }
            #screenshot-overlay .flash {
                position: absolute;
                top: 0; left: 0; right: 0; bottom: 0;
                background: white;
                animation: ss-flash 0.35s ease-out forwards;
            }
            #screenshot-overlay .border-frame {
                position: absolute;
                top: 0; left: 0; right: 0; bottom: 0;
                border: 4px solid #6366f1;
                border-radius: 0;
                animation: ss-border 0.8s ease-out forwards;
                box-shadow: inset 0 0 60px rgba(99, 102, 241, 0.15);
            }
            #screenshot-overlay .badge {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%) scale(0.8);
                background: rgba(20, 20, 30, 0.88);
                backdrop-filter: blur(12px);
                color: white;
                padding: 14px 28px;
                border-radius: 14px;
                font-size: 16px;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: 10px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.4), 0 0 0 1px rgba(99, 102, 241, 0.3);
                animation: ss-badge 0.8s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
                letter-spacing: 0.3px;
            }
            #screenshot-overlay .badge .icon {
                font-size: 22px;
                filter: drop-shadow(0 0 4px rgba(99, 102, 241, 0.5));
            }
            #screenshot-overlay .shutter-line {
                position: absolute;
                top: 0; left: 0; right: 0;
                height: 3px;
                background: linear-gradient(90deg, transparent, #6366f1, #818cf8, #6366f1, transparent);
                animation: ss-shutter 0.5s ease-out forwards;
            }
            @keyframes ss-flash {
                0% { opacity: 0.7; }
                100% { opacity: 0; }
            }
            @keyframes ss-border {
                0% { opacity: 1; border-width: 4px; }
                40% { opacity: 1; }
                100% { opacity: 0; border-width: 0; }
            }
            @keyframes ss-badge {
                0% { opacity: 0; transform: translate(-50%, -50%) scale(0.6); }
                30% { opacity: 1; transform: translate(-50%, -50%) scale(1.05); }
                50% { transform: translate(-50%, -50%) scale(1); }
                80% { opacity: 1; }
                100% { opacity: 0; transform: translate(-50%, -50%) scale(0.95); }
            }
            @keyframes ss-shutter {
                0% { top: 0; opacity: 1; }
                100% { top: 100%; opacity: 0; }
            }

            /* ── Mouse Cursor Bubble ── */
            #cursor-bubble {
                position: fixed;
                width: 28px;
                height: 28px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(99, 102, 241, 0.5) 0%, rgba(99, 102, 241, 0.15) 60%, transparent 70%);
                border: 2px solid rgba(99, 102, 241, 0.6);
                transform: translate(-50%, -50%);
                pointer-events: none;
                z-index: 10001;
                display: none;
                transition: left 0.08s ease-out, top 0.08s ease-out, opacity 0.3s ease;
                box-shadow: 0 0 16px rgba(99, 102, 241, 0.3);
            }
            #cursor-bubble.visible {
                display: block;
                animation: bubble-appear 0.3s ease-out forwards;
            }
            #cursor-bubble .trail {
                position: absolute;
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: rgba(99, 102, 241, 0.3);
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
            }
            @keyframes bubble-appear {
                0% { opacity: 0; transform: translate(-50%, -50%) scale(0.3); }
                100% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
            }
        </style>
    </head>
    <body>
        <div id="cursor-bubble"></div>
        <div id="screenshot-overlay">
            <div class="flash"></div>
            <div class="border-frame"></div>
            <div class="shutter-line"></div>
            <div class="badge"><span class="icon">📸</span> Captura tomada</div>
        </div>
        <script>
            const ssEl = document.getElementById('screenshot-overlay');
            const cursorBubble = document.getElementById('cursor-bubble');
            let hideTimer = null;
            let bubbleHideTimer = null;

            window.showClick = (x, y, type) => {
                type = type || 'click';
                // Create a click-point element at position
                const el = document.createElement('div');
                el.className = 'click-point ' + type;
                el.style.left = x + 'px';
                el.style.top = y + 'px';
                el.innerHTML = 
                    '<div class="dot"></div>' +
                    '<div class="ripple ripple-1"></div>' +
                    '<div class="ripple ripple-2"></div>' +
                    '<div class="ripple ripple-3"></div>' +
                    '<div class="coord-label">(' + x + ', ' + y + ')</div>';
                document.body.appendChild(el);
                setTimeout(function() { el.remove(); }, 1200);

                // Also show/move cursor bubble
                window.showCursorAt(x, y);
            };
            
            window.showScreenshot = () => {
                ssEl.classList.remove('active');
                void ssEl.offsetWidth;
                ssEl.classList.add('active');
                if (hideTimer) clearTimeout(hideTimer);
                hideTimer = setTimeout(function() { ssEl.classList.remove('active'); }, 900);
            };

            window.showCursorAt = (x, y) => {
                cursorBubble.style.left = x + 'px';
                cursorBubble.style.top = y + 'px';
                cursorBubble.classList.add('visible');
                if (bubbleHideTimer) clearTimeout(bubbleHideTimer);
                bubbleHideTimer = setTimeout(function() {
                    cursorBubble.classList.remove('visible');
                }, 3000);
            };

            window.hideCursor = () => {
                cursorBubble.classList.remove('visible');
            };
        </script>
    </body>
    </html>
    `);

    // Sin forward: esta ventana nunca es interactiva y el hook global de mouse
    // que forward instala en Windows hace saltar otras ventanas al arrastrarlas
    // (electron#35030).
    actionOverlayWindow.setIgnoreMouseEvents(true);
    actionOverlayWindow.setVisibleOnAllWorkspaces(true);
    actionOverlayWindow.hide();

    actionOverlayWindow.on('closed', () => {
        actionOverlayWindow = null;
    });
}

ipcMain.handle('set-window-opacity', (_, opacity) => {
    if (mainWindow) {
        mainWindow.setOpacity(Math.max(0.5, Math.min(1.0, opacity)));
    }
});

ipcMain.handle('show-click-indicator', async (_, x, y, type) => {
    try {
        if (!actionOverlayWindow || actionOverlayWindow.isDestroyed()) {
            createActionOverlayWindow();
            // Wait for page ready instead of fixed timeout
            await new Promise(resolve => {
                if (!actionOverlayWindow || actionOverlayWindow.isDestroyed()) return resolve();
                if (actionOverlayWindow.webContents.isLoading()) {
                    actionOverlayWindow.webContents.once('did-finish-load', resolve);
                    setTimeout(resolve, 600); // fallback
                } else {
                    resolve();
                }
            });
        }
        if (actionOverlayWindow && !actionOverlayWindow.isDestroyed()) {
            actionOverlayWindow.show();
            const safeType = String(type || 'click').replace(/[^a-z_]/g, '');
            await actionOverlayWindow.webContents.executeJavaScript(
                `window.showClick && window.showClick(${Number(x) || 0}, ${Number(y) || 0}, '${safeType}')`
            );
            // Keep visible for animation duration, then hide
            setTimeout(() => {
                if (actionOverlayWindow && !actionOverlayWindow.isDestroyed()) {
                    actionOverlayWindow.hide();
                }
            }, 1400);
        }
    } catch (err) {
        console.error('[IPC] show-click-indicator error:', err.message);
    }
});

ipcMain.handle('show-screenshot-overlay', async () => {
    try {
        // Ocultar ventana principal para que no salga en la captura
        if (mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible()) {
            mainWindow.hide();
        }
        // Pequeña pausa para que el OS aplique el ocultamiento
        await new Promise(resolve => setTimeout(resolve, 80));

        if (!actionOverlayWindow || actionOverlayWindow.isDestroyed()) {
            createActionOverlayWindow();
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        if (actionOverlayWindow && !actionOverlayWindow.isDestroyed()) {
            actionOverlayWindow.show();
            await actionOverlayWindow.webContents.executeJavaScript(`window.showScreenshot && window.showScreenshot()`);
            setTimeout(() => {
                if (actionOverlayWindow && !actionOverlayWindow.isDestroyed()) {
                    actionOverlayWindow.hide();
                }
                // Restaurar ventana principal después del overlay
                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.show();
                }
            }, 1200);
        } else {
            // Si no hay overlay, restaurar la ventana
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.show();
            }
        }
    } catch (err) {
        console.error('[IPC] show-screenshot-overlay error:', err.message);
        // Siempre restaurar la ventana en caso de error
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.show();
        }
    }
});

ipcMain.handle('set-executing-mode', (_, active) => {
    if (mainWindow) {
        if (active) {
            mainWindow.hide();
        } else {
            mainWindow.show();
        }
    }
    setOverlayInteractionLocked(active);
});

ipcMain.handle('show-cursor-bubble', async (_, x, y) => {
    try {
        if (!actionOverlayWindow || actionOverlayWindow.isDestroyed()) {
            createActionOverlayWindow();
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        if (actionOverlayWindow && !actionOverlayWindow.isDestroyed()) {
            actionOverlayWindow.show();
            await actionOverlayWindow.webContents.executeJavaScript(
                `window.showCursorAt && window.showCursorAt(${Number(x) || 0}, ${Number(y) || 0})`
            );
        }
    } catch (err) {
        console.error('[IPC] show-cursor-bubble error:', err.message);
    }
});

// ── App Lifecycle ────────────────────────────────────────────

app.on('second-instance', () => {
    focusPrimaryWindow();
});

app.whenReady().then(async () => {
    watchConfigFiles();

    // 1. Lanzar backend Python como proceso hijo
    console.log('[App] Iniciando backend...');
    const backendOk = await startBackend();
    if (backendOk) {
        console.log('[App] Backend listo');
    } else {
        console.warn('[App] Backend no confirmó arranque — la UI intentará reconectar');
    }

    // Protocolo gmini-skin:// para servir assets de data/skins/<id>/...
    protocol.handle('gmini-skin', (request) => {
        try {
            const url = new URL(request.url);
            const skinId = url.hostname;
            const relPath = decodeURIComponent(url.pathname.replace(/^\/+/, ''));
            const entry = scanAvailableSkinsCached().find((s) => s.id === skinId);
            if (!entry || !entry.dir) {
                return new Response('Not found', { status: 404 });
            }
            const baseDir = path.resolve(DATA_SKINS_DIR, entry.dir);
            const filePath = path.resolve(baseDir, relPath);
            if ((filePath !== baseDir && !filePath.startsWith(baseDir + path.sep)) || !fs.existsSync(filePath)) {
                return new Response('Not found', { status: 404 });
            }
            return net.fetch(`file://${filePath.replace(/\\/g, '/')}`);
        } catch (err) {
            console.error(`[Skin] Error sirviendo gmini-skin://: ${err.message}`);
            return new Response('Error', { status: 500 });
        }
    });

    // 2. Crear UI
    createMainWindow();
    createOverlayWindow();
    createSkinWindow();
    createActionOverlayWindow();  // Pre-crear overlay de acciones
    createTray();
    applyAppPreferences(appPreferences);
    setSkinMode(characterPreferences.mode);

    // Global shortcuts
    globalShortcut.register('Alt+G', () => {
        if (mainWindow) {
            if (mainWindow.isVisible()) {
                mainWindow.hide();
            } else {
                mainWindow.show();
                mainWindow.focus();
            }
        }
    });

    globalShortcut.register('Alt+Shift+G', () => {
        toggleOverlay(!isOverlayMode);
    });

    // Kill switch — Ctrl+Shift+Esc ya es del OS Task Manager, usar Ctrl+Shift+Q
    globalShortcut.register('Ctrl+Shift+Q', () => {
        app.isQuitting = true;
        app.quit();
    });
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

app.on('will-quit', () => {
    clearTimeout(configReloadTimer);
    clearTimeout(overlayStateSaveTimer);
    clearTimeout(skinStateSaveTimer);
    unwatchConfigFiles();
    persistOverlayStateToDisk();
    persistSkinStateToDisk();
    globalShortcut.unregisterAll();
    stopBackend();
});

app.on('activate', () => {
    if (mainWindow === null) {
        createMainWindow();
    }
    if (overlayWindow === null) {
        createOverlayWindow();
    }
    if (skinWindow === null) {
        createSkinWindow();
    }
    focusPrimaryWindow();
});
