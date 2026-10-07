/**
 * G-Mini Agent — Primer arranque de la app instalada.
 *
 * El instalador trae el núcleo (backend/, data/) y uv, no Python: la primera
 * vez se crea un entorno de Python 3.13 con los componentes de escritorio en
 * la carpeta local del usuario, mostrando el progreso. Las siguientes veces
 * solo se comprueba la huella de los requirements y se arranca directo; si una
 * actualización los cambia, se completa lo que falte.
 */

const { BrowserWindow, ipcMain, shell } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PYTHON_VERSION = '3.13';
const REQUIREMENTS = ['requirements-desktop.txt', 'requirements-server.txt'];
const STEPS = [
    { id: 'python', title: 'Python 3.13', detail: 'Intérprete propio de G-Mini, aparte del que tengas instalado.' },
    { id: 'packages', title: 'Componentes del núcleo', detail: 'Proveedores de IA, voz, memoria y control del escritorio.' },
    { id: 'check', title: 'Comprobación', detail: 'Que el núcleo arranque con lo instalado.' },
];

function runtimeLayout({ codeRoot, localRoot, resourcesPath }) {
    const win = process.platform === 'win32';
    const envDir = path.join(localRoot, 'python');
    return {
        codeRoot,
        envDir,
        python: win ? path.join(envDir, 'Scripts', 'python.exe') : path.join(envDir, 'bin', 'python'),
        stampFile: path.join(envDir, '.gmini-runtime'),
        logFile: path.join(localRoot, 'logs', 'setup.log'),
        uv: path.join(resourcesPath, 'uv', win ? 'uv.exe' : 'uv'),
        uvEnv: {
            UV_PYTHON_INSTALL_DIR: path.join(localRoot, 'python-dist'),
            UV_CACHE_DIR: path.join(localRoot, 'cache', 'uv'),
            UV_PYTHON_PREFERENCE: 'only-managed',
            UV_NO_CONFIG: '1',
            UV_NO_PROGRESS: '1',
        },
    };
}

function requirementsFingerprint(codeRoot) {
    const hash = crypto.createHash('sha256').update(PYTHON_VERSION);
    for (const name of REQUIREMENTS) {
        hash.update(fs.readFileSync(path.join(codeRoot, 'backend', name)));
    }
    return hash.digest('hex').slice(0, 16);
}

function isRuntimeReady(layout) {
    try {
        return fs.existsSync(layout.python)
            && fs.readFileSync(layout.stampFile, 'utf8').trim() === requirementsFingerprint(layout.codeRoot);
    } catch {
        return false;
    }
}

class SetupLog {
    constructor(file) {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        this.file = file;
        this.tail = [];
        fs.writeFileSync(file, `G-Mini: preparación del entorno (${new Date().toISOString()})\n`);
    }

    write(line) {
        const clean = String(line).replace(/\x1b\[[0-9;]*m/g, '').trimEnd();
        if (!clean) return;
        fs.appendFileSync(this.file, `${clean}\n`);
        this.tail.push(clean);
        if (this.tail.length > 8) this.tail.shift();
    }
}

function run(command, args, { env, cwd, log, onLine }) {
    return new Promise((resolve, reject) => {
        log.write(`$ ${path.basename(command)} ${args.join(' ')}`);
        const child = spawn(command, args, {
            cwd,
            env: { ...process.env, ...env },
            windowsHide: true,
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        const feed = (chunk) => {
            for (const line of chunk.toString('utf8').split(/\r?\n/)) {
                if (!line.trim()) continue;
                log.write(line);
                if (onLine) onLine(line.trim());
            }
        };
        child.stdout.on('data', feed);
        child.stderr.on('data', feed);
        child.on('error', reject);
        child.on('exit', (code) => {
            if (code === 0) resolve();
            else reject(new Error(`${path.basename(command)} terminó con código ${code}`));
        });
    });
}

function friendlyError(err, log) {
    const text = `${err.message}\n${log.tail.join('\n')}`.toLowerCase();
    if (/(dns|resolve|connect|timed out|network|tls|certificate|proxy)/.test(text)) {
        return 'No pude descargar los componentes. Revisa tu conexión a internet (o el proxy) y reintenta.';
    }
    if (/(no space|espacio|disk full|os error 112)/.test(text)) {
        return 'No hay espacio suficiente en el disco. Se necesitan unos 1,5 GB libres.';
    }
    if (/(access is denied|acceso denegado|permission denied|os error 5)/.test(text)) {
        return 'Windows bloqueó el acceso a la carpeta del entorno. Cierra otras copias de G-Mini o el antivirus que la esté revisando y reintenta.';
    }
    return 'Algo falló al preparar el entorno. El registro tiene el detalle.';
}

function createSetupWindow(iconPath) {
    const win = new BrowserWindow({
        width: 560,
        height: 440,
        resizable: false,
        maximizable: false,
        fullscreenable: false,
        frame: false,
        show: false,
        backgroundColor: '#0f1115',
        title: 'Preparando G-Mini',
        icon: iconPath,
        webPreferences: {
            preload: path.join(__dirname, 'setup-preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
        },
    });
    win.loadFile(path.join(__dirname, 'src', 'setup.html'));
    win.once('ready-to-show', () => win.show());
    return win;
}

/**
 * Devuelve la ruta del Python listo para el núcleo. En desarrollo no se llama.
 * Si el usuario cierra la ventana de preparación, resuelve null.
 */
async function ensurePythonRuntime({ codeRoot, localRoot, resourcesPath, iconPath }) {
    const layout = runtimeLayout({ codeRoot, localRoot, resourcesPath });
    if (isRuntimeReady(layout)) return layout.python;

    const win = createSetupWindow(iconPath);
    const state = { steps: STEPS.map((s) => ({ ...s, status: 'pending', note: '' })), error: null, tail: [] };
    let log = null;

    const push = () => {
        if (log) state.tail = log.tail.slice(-5);
        if (!win.isDestroyed()) win.webContents.send('setup:state', state);
    };
    const mark = (id, status, note = '') => {
        const step = state.steps.find((s) => s.id === id);
        step.status = status;
        if (note) step.note = note;
        push();
    };

    const attempt = async () => {
        log = new SetupLog(layout.logFile);
        state.error = null;
        state.steps.forEach((s) => { s.status = 'pending'; s.note = ''; });
        push();
        const opts = { env: layout.uvEnv, cwd: path.join(codeRoot, 'backend'), log };
        let current = 'python';
        try {
            mark('python', 'running');
            await run(layout.uv, ['venv', '--python', PYTHON_VERSION, '--allow-existing', layout.envDir], opts);
            mark('python', 'done');

            current = 'packages';
            mark('packages', 'running', 'Calculando qué falta...');
            await run(layout.uv, ['pip', 'install', '--python', layout.python, '-r', 'requirements-desktop.txt'], {
                ...opts,
                onLine: (line) => {
                    const m = /^(Resolved|Prepared|Installed|Uninstalled|Audited) (\d+) package/.exec(line);
                    if (!m) return;
                    const verbs = {
                        Resolved: `${m[2]} paquetes por revisar`,
                        Prepared: `${m[2]} paquetes descargados`,
                        Installed: `${m[2]} paquetes instalados`,
                        Uninstalled: `${m[2]} versiones viejas retiradas`,
                        Audited: `${m[2]} paquetes ya estaban al día`,
                    };
                    mark('packages', 'running', verbs[m[1]]);
                },
            });
            mark('packages', 'done');

            current = 'check';
            mark('check', 'running');
            await run(layout.python, ['-c', 'import fastapi, socketio, openai, google.genai, edge_tts; print("ok")'], opts);
            fs.writeFileSync(layout.stampFile, requirementsFingerprint(codeRoot));
            mark('check', 'done');
            return true;
        } catch (err) {
            log.write(`ERROR: ${err.message}`);
            mark(current, 'failed');
            state.error = friendlyError(err, log);
            push();
            return false;
        }
    };

    return new Promise((resolve) => {
        let finished = false;
        const cleanup = () => {
            ipcMain.removeHandler('setup:retry');
            ipcMain.removeHandler('setup:open-log');
            ipcMain.removeHandler('setup:quit');
            ipcMain.removeHandler('setup:get-state');
        };
        const finish = (value) => {
            if (finished) return;
            finished = true;
            cleanup();
            if (!win.isDestroyed()) win.close();
            resolve(value);
        };
        const go = async () => {
            if (await attempt()) setTimeout(() => finish(layout.python), 700);
        };

        ipcMain.handle('setup:get-state', () => state);
        ipcMain.handle('setup:retry', () => { if (!finished) void go(); });
        ipcMain.handle('setup:open-log', () => shell.openPath(layout.logFile));
        ipcMain.handle('setup:quit', () => finish(null));
        win.on('closed', () => finish(null));
        win.webContents.once('did-finish-load', () => { void go(); });
    });
}

module.exports = { ensurePythonRuntime, runtimeLayout, isRuntimeReady };
