import * as THREE from 'three';
import { EnergyBallSkin } from './energyBallSkin.js';
import { VrmSkin } from './vrmSkin.js';
import { GlbStaticSkin } from './glbStaticSkin.js';
import { Sprite2DSkin } from './sprite2dSkin.js';
import { skinChat } from './skinChat.js';

const gmini = window.gmini;

const canvas = document.getElementById('skin-canvas');
const controls = document.getElementById('skin-controls');
const btnClose = document.getElementById('btn-skin-close');
const btnMinimize = document.getElementById('btn-skin-minimize');
const btnMic = document.getElementById('btn-skin-mic');

const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);

// Iluminacion compartida: los materiales MToon de los VRM (y los GLB estandar)
// se renderizan negros sin luces. Intensidades en unidades fisicas (three r155+),
// mismo setup que el ejemplo oficial de @pixiv/three-vrm.
const keyLight = new THREE.DirectionalLight(0xffffff, Math.PI);
keyLight.position.set(1.0, 1.0, 1.0).normalize();
scene.add(keyLight);
const fillLight = new THREE.AmbientLight(0xffffff, 0.4 * Math.PI);
scene.add(fillLight);

let activeSkin = null;
let currentSkinId = null;
let availableSkins = [];

let runtime = {
    status: 'idle',
    mouth: 0,
    emotion: 'neutral',
    audioHintMs: 0,
};

function resizeRenderer() {
    const width = window.innerWidth || 1;
    const height = window.innerHeight || 1;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
}

window.addEventListener('resize', resizeRenderer);
resizeRenderer();

function createSkinInstance(skinId) {
    const entry = availableSkins.find((s) => s.id === skinId);
    const manifest = entry?.manifest || { id: skinId };

    switch (entry?.type) {
        case 'vrm':
            return new VrmSkin(manifest);
        case 'glb':
            return new GlbStaticSkin(manifest);
        case 'sprite2d':
            return new Sprite2DSkin(manifest);
        case 'procedural':
        default:
            return new EnergyBallSkin();
    }
}

let skinLoadSeq = 0;

async function setActiveSkin(skinId) {
    const nextId = skinId || 'energy-ball';
    // currentSkinId se fija ANTES del await: init() y el broadcast skin-state
    // llegan casi a la vez al arrancar y sin esto se cargaban dos instancias
    // del mismo VRM superpuestas (la fantasma quedaba congelada en T-pose).
    if (nextId === currentSkinId) return;
    currentSkinId = nextId;
    const seq = ++skinLoadSeq;

    if (activeSkin) {
        activeSkin.dispose();
        activeSkin = null;
    }

    let skin = createSkinInstance(nextId);
    try {
        await skin.load({ THREE, scene, camera });
    } catch (err) {
        console.warn(`[Skin] Fallo al cargar '${nextId}', usando bola de energia:`, err);
        skin = new EnergyBallSkin();
        await skin.load({ THREE, scene, camera });
    }
    if (seq !== skinLoadSeq) {
        // Mientras cargaba, se pidio otra skin: descartar esta carga.
        skin.dispose();
        return;
    }
    skin.setStatus(runtime.status);
    activeSkin = skin;
}

let lastTime = performance.now();
function animate(now) {
    requestAnimationFrame(animate);
    const dt = Math.min(0.1, (now - lastTime) / 1000);
    lastTime = now;

    if (activeSkin) {
        activeSkin.update(dt, runtime);
    }
    renderer.render(scene, camera);
}
requestAnimationFrame(animate);

// ── IPC: estado de skin (modo, config, lista de skins) ──────────────────────
if (gmini && typeof gmini.onSkinState === 'function') {
    gmini.onSkinState((state) => {
        if (Array.isArray(state?.skins)) {
            availableSkins = state.skins;
        }
        const skinId = state?.characterConfig?.skin || 'energy-ball';
        void setActiveSkin(skinId);
        if (state?.characterRuntime) {
            applyRuntime(state.characterRuntime);
        }
        if (typeof state?.interactive === 'boolean' && state.interactive !== isInteractive) {
            isInteractive = state.interactive;
            controls.classList.toggle('visible', state.interactive);
        }
    });
}

// ── IPC: runtime (status, visemas, mouth, emocion) ──────────────────────────
if (gmini && typeof gmini.onOverlayCharacterRuntime === 'function') {
    gmini.onOverlayCharacterRuntime((payload) => {
        applyRuntime(payload);
    });
}

function applyRuntime(payload = {}) {
    runtime = { ...runtime, ...payload };
    if (activeSkin) {
        if (typeof payload.status === 'string') {
            activeSkin.setStatus(payload.status);
        }
        if (typeof payload.emotion === 'string') {
            activeSkin.setEmotion(payload.emotion, 1);
        }
    }
}

// ── Estado inicial ────────────────────────────────────────────────────────
async function init() {
    if (!gmini || typeof gmini.skinGetState !== 'function') {
        await setActiveSkin('energy-ball');
        return;
    }
    const state = await gmini.skinGetState();
    if (Array.isArray(state?.skins)) {
        availableSkins = state.skins;
    }
    const skinId = state?.characterConfig?.skin || 'energy-ball';
    await setActiveSkin(skinId);
    if (state?.characterRuntime) {
        applyRuntime(state.characterRuntime);
    }
}
void init();

// ── Interaccion: hover (toggle click-through), drag y resize con rueda ──────
let isInteractive = false;
let voiceIsActive = false;
let isDragging = false;
let dragMoved = false;
let dragStart = { x: 0, y: 0 };
let pendingDx = 0;
let pendingDy = 0;
let dragRafId = 0;

function setInteractive(next) {
    if (isInteractive === next) return;
    isInteractive = next;
    controls.classList.toggle('visible', next);
    if (gmini && typeof gmini.skinSetInteractive === 'function') {
        void gmini.skinSetInteractive(next);
    }
}

document.addEventListener('mouseenter', () => setInteractive(true));
document.addEventListener('mouseover', () => setInteractive(true));
document.addEventListener('mouseleave', () => {
    if (!isDragging && !skinChat.isChatOpen() && !voiceIsActive) setInteractive(false);
});

document.addEventListener('mousedown', (event) => {
    if (event.target === btnClose || event.target === btnMinimize || event.target === btnMic) return;
    isDragging = true;
    dragMoved = false;
    dragStart = { x: event.screenX, y: event.screenY };
});

function flushDragMove() {
    dragRafId = 0;
    if (pendingDx === 0 && pendingDy === 0) return;
    const dx = pendingDx;
    const dy = pendingDy;
    pendingDx = 0;
    pendingDy = 0;
    if (gmini && typeof gmini.skinMoveBy === 'function') {
        void gmini.skinMoveBy(dx, dy);
    }
}

document.addEventListener('mousemove', (event) => {
    if (!isDragging) return;
    if ((event.buttons & 1) === 0) {
        isDragging = false;
        dragMoved = false;
        return;
    }
    const dx = event.screenX - dragStart.x;
    const dy = event.screenY - dragStart.y;
    if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return;
    dragMoved = true;
    dragStart = { x: event.screenX, y: event.screenY };
    pendingDx += dx;
    pendingDy += dy;
    if (!dragRafId) {
        dragRafId = requestAnimationFrame(flushDragMove);
    }
});

document.addEventListener('mouseup', () => {
    if (isDragging && !dragMoved) {
        skinChat.toggle();
    }
    isDragging = false;
    dragMoved = false;
});

window.addEventListener('blur', () => {
    isDragging = false;
    dragMoved = false;
});

document.addEventListener('wheel', (event) => {
    event.preventDefault();
    const delta = event.deltaY > 0 ? -0.05 : 0.05;
    if (gmini && typeof gmini.skinResizeBy === 'function') {
        void gmini.skinResizeBy(delta);
    }
}, { passive: false });

btnClose.addEventListener('click', () => {
    skinChat.close();
    if (gmini && typeof gmini.skinSetMode === 'function') {
        void gmini.skinSetMode('chat');
    }
});

btnMinimize.addEventListener('click', () => {
    skinChat.close();
    if (gmini && typeof gmini.skinMinimize === 'function') {
        void gmini.skinMinimize();
    }
});

// ── Boton mic: activa/desactiva voz en tiempo real (proxy a mainWindow) ──────
btnMic.addEventListener('click', () => {
    if (gmini && typeof gmini.skinVoiceToggle === 'function') {
        void gmini.skinVoiceToggle();
    }
});

if (gmini && typeof gmini.onSkinVoiceState === 'function') {
    gmini.onSkinVoiceState(({ active, available } = {}) => {
        voiceIsActive = !!active;
        btnMic.classList.toggle('skin-mic-hidden', !available);
        btnMic.classList.toggle('skin-mic-active', !!active);
        btnMic.title = active ? 'Detener voz en tiempo real' : 'Activar voz en tiempo real';
        if (active) setInteractive(true);
    });
}
