import { SkinBase } from './skinBase.js';

const FRAMES = ['idle', 'talk', 'blink', 'blink_talk'];
const EMOTIONS = ['happy', 'sad', 'angry', 'surprised', 'relaxed'];
const MOUTH_THRESHOLD = 0.05;
const MOUTH_HOLD_MS = 100;
const BLINK_DURATION_S = 0.15;
const VIEWPORT_FILL_RATIO = 0.95;
const CAMERA_DISTANCE = 2;
const CAMERA_FOV_DEG = 45;

// Skin 2D: plano texturizado en THREE que alterna entre sprites PNG
// (idle/talk/blink/blink_talk + emociones opcionales) segun runtime.mouth
// y un parpadeo periodico, igual que vrmSkin pero sin rig 3D.
export class Sprite2DSkin extends SkinBase {
    constructor(manifest = {}) {
        super();
        this.manifest = manifest;
        this.scene = null;
        this.mesh = null;
        this.textures = {};
        this.currentFrameKey = 'idle';
        this.status = 'idle';
        this.aspect = 1;

        this.blinkTimer = 0;
        this.nextBlink = 3 + Math.random() * 2;
        this.isBlinking = false;

        this.talking = false;
        this.mouthHoldMs = 0;

        this.emotion = 'neutral';
        this.emotionIntensity = 0;
    }

    async load(ctx) {
        const { THREE, scene, camera } = ctx;
        this.THREE = THREE;
        this.scene = scene;

        const sprites = this.manifest.sprites || {};
        if (!sprites.idle) {
            throw new Error('La skin 2D no define sprites.idle');
        }

        const baseUrl = `gmini-skin://${this.manifest.id}/`;
        const loader = new THREE.TextureLoader();
        const pixelArt = !!this.manifest.pixelArt;

        const loadTexture = async (relPath) => {
            const tex = await loader.loadAsync(baseUrl + relPath);
            tex.colorSpace = THREE.SRGBColorSpace;
            tex.generateMipmaps = false;
            tex.minFilter = pixelArt ? THREE.NearestFilter : THREE.LinearFilter;
            tex.magFilter = pixelArt ? THREE.NearestFilter : THREE.LinearFilter;
            return tex;
        };

        for (const frame of FRAMES) {
            const rel = sprites[frame];
            if (!rel) continue;
            try {
                this.textures[frame] = await loadTexture(rel);
            } catch (err) {
                console.warn(`[Sprite2D] No se pudo cargar frame '${frame}':`, err);
            }
        }

        const emotions = sprites.emotions || {};
        for (const emo of EMOTIONS) {
            const rel = emotions[emo];
            if (!rel) continue;
            try {
                this.textures[emo] = await loadTexture(rel);
            } catch (err) {
                console.warn(`[Sprite2D] No se pudo cargar emocion '${emo}':`, err);
            }
        }

        if (!this.textures.idle) {
            throw new Error('No se pudo cargar el sprite idle');
        }

        const idleImg = this.textures.idle.image;
        this.aspect = (idleImg?.width && idleImg?.height) ? (idleImg.width / idleImg.height) : 1;

        camera.fov = CAMERA_FOV_DEG;
        camera.position.set(0, 0, CAMERA_DISTANCE);
        camera.lookAt(0, 0, 0);
        camera.updateProjectionMatrix();

        const visibleHeight = 2 * CAMERA_DISTANCE * Math.tan((CAMERA_FOV_DEG / 2) * Math.PI / 180);
        const planeHeight = visibleHeight * VIEWPORT_FILL_RATIO;
        const planeWidth = planeHeight * this.aspect;

        const geometry = new THREE.PlaneGeometry(planeWidth, planeHeight);
        const material = new THREE.MeshBasicMaterial({
            map: this.textures.idle,
            transparent: true,
            depthWrite: false,
        });
        this.mesh = new THREE.Mesh(geometry, material);
        this.currentFrameKey = 'idle';
        scene.add(this.mesh);
    }

    _frameOrFallback(key, fallbacks) {
        if (this.textures[key]) return key;
        for (const fb of fallbacks) {
            if (this.textures[fb]) return fb;
        }
        return 'idle';
    }

    _resolveFrame() {
        if (this.talking && this.isBlinking) {
            return this._frameOrFallback('blink_talk', ['talk', 'blink', 'idle']);
        }
        if (this.talking) {
            return this._frameOrFallback('talk', ['idle']);
        }
        if (this.isBlinking) {
            return this._frameOrFallback('blink', ['idle']);
        }
        if (this.emotion !== 'neutral' && this.emotionIntensity > 0) {
            return this._frameOrFallback(this.emotion, ['idle']);
        }
        return 'idle';
    }

    update(dt, runtime = {}) {
        if (!this.mesh) return;

        // Boca: RMS llega a ~20Hz, mantenemos 'talking' un poco al bajar
        // para no parpadear entre talk/idle en cada frame silencioso.
        const mouth = Math.max(0, Math.min(1, Number(runtime.mouth) || 0));
        if (mouth > MOUTH_THRESHOLD) {
            this.talking = true;
            this.mouthHoldMs = MOUTH_HOLD_MS;
        } else if (this.mouthHoldMs > 0) {
            this.mouthHoldMs -= dt * 1000;
            this.talking = this.mouthHoldMs > 0;
        } else {
            this.talking = false;
        }

        // Parpadeo periodico: cada 3-5s, dura 150ms.
        this.blinkTimer += dt;
        if (this.isBlinking) {
            if (this.blinkTimer >= BLINK_DURATION_S) {
                this.isBlinking = false;
                this.blinkTimer = 0;
                this.nextBlink = 3 + Math.random() * 2;
            }
        } else if (this.blinkTimer >= this.nextBlink) {
            this.isBlinking = true;
            this.blinkTimer = 0;
        }

        // Decay de la emocion hacia neutral (~4s), igual que vrmSkin.
        if (this.emotion !== 'neutral' && this.emotionIntensity > 0) {
            this.emotionIntensity = Math.max(0, this.emotionIntensity - dt / 4);
            if (this.emotionIntensity === 0) this.emotion = 'neutral';
        }

        const frame = this._resolveFrame();
        if (frame !== this.currentFrameKey) {
            this.mesh.material.map = this.textures[frame] || this.textures.idle;
            this.mesh.material.needsUpdate = true;
            this.currentFrameKey = frame;
        }
    }

    setEmotion(name, intensity = 1) {
        if (!EMOTIONS.includes(name)) return;
        this.emotion = name;
        this.emotionIntensity = Math.max(0, Math.min(1, intensity));
    }

    setStatus(status) {
        this.status = status || 'idle';
    }

    getBoundsPx() {
        const height = 360;
        return { width: Math.round(height * this.aspect) || 320, height };
    }

    dispose() {
        if (this.mesh) {
            this.scene.remove(this.mesh);
            this.mesh.geometry.dispose();
            this.mesh.material.dispose();
            this.mesh = null;
        }
        for (const tex of Object.values(this.textures)) {
            tex.dispose();
        }
        this.textures = {};
    }
}
