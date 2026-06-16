import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { SkinBase } from './skinBase.js';
import { loadMixamoAnimation } from './loadMixamoAnimation.js';

const STATUS_TO_ACTION = {
    idle: 'idle',
    thinking: 'thinking',
    calling: 'thinking',
    responding: 'talk',
    executing: 'wave',
};

const EMOTIONS = ['happy', 'sad', 'angry', 'surprised', 'relaxed'];

// Skin VRM (VRoid + animaciones Mixamo retargeteadas).
// Lipsync via expresion 'aa' (amplitud de voz) y emociones via expressionManager,
// con decay automatico hacia 'neutral'.
export class VrmSkin extends SkinBase {
    constructor(manifest = {}) {
        super();
        this.manifest = manifest;
        this.vrm = null;
        this.mixer = null;
        this.actions = {};
        this.currentAction = null;
        this.scene = null;
        this.status = 'idle';
        this.blinkTimer = 0;
        this.nextBlink = 3 + Math.random() * 2;
        this.emotion = 'neutral';
        this.emotionIntensity = 0;
        this.idleTime = 0;
        this.idleBones = null;

        // Capa procedural (solo cuando no hay animaciones Mixamo):
        // pose de reposo por brazo (signo segun convencion VRM0/VRM1 detectada),
        // peso de gesticulacion al hablar y gesto one-shot activo (saludo/emote).
        this.armPose = null;
        this.talking = false;
        this.mouthHoldMs = 0;
        this.talkWeight = 0;
        this.gesture = null;
    }

    _startGesture(name, dur) {
        this.gesture = { name, t: 0, dur };
    }

    // Los VRM se exportan en T-pose; bajamos los brazos (~66 grados) para una pose
    // de reposo natural. El signo de rotation.z depende de la convencion VRM0/VRM1,
    // asi que se verifica contra la posicion world del codo y se invierte si subio.
    // El signo/angulo final por brazo se guarda en this.armPose para que las capas
    // de gesticulacion (hablar, saludo, emotes) operen en la direccion correcta.
    _applyRestPose() {
        const humanoid = this.vrm?.humanoid;
        if (!humanoid) return;
        const THREE = this.THREE;
        const angle = 1.15;
        const posA = new THREE.Vector3();
        const posB = new THREE.Vector3();
        this.armPose = {};

        for (const [side, sign] of [['left', -1], ['right', 1]]) {
            const upper = humanoid.getNormalizedBoneNode(`${side}UpperArm`);
            const lower = humanoid.getNormalizedBoneNode(`${side}LowerArm`);
            if (!upper) continue;
            let finalSign = sign;
            const probe = lower || upper.children[0];
            if (probe) {
                const beforeY = probe.getWorldPosition(posA).y;
                upper.rotation.z = sign * angle;
                upper.updateWorldMatrix(true, true);
                const afterY = probe.getWorldPosition(posB).y;
                if (afterY > beforeY) {
                    finalSign = -sign;
                    upper.rotation.z = finalSign * angle;
                    upper.updateWorldMatrix(true, true);
                }
            } else {
                upper.rotation.z = sign * angle;
            }
            this.armPose[side] = { sign: finalSign, base: finalSign * angle };
        }
    }

    // Idle procedural por capas para VRM sin animaciones Mixamo:
    //   1. respiracion + balanceo (siempre)
    //   2. gesticulacion de manos al hablar (peso suavizado por runtime.mouth)
    //   3. gesto one-shot con envolvente: saludo inicial y emotes corporales
    _updateProceduralIdle(dt, runtime = {}) {
        const humanoid = this.vrm?.humanoid;
        if (!humanoid) return;
        if (!this.idleBones) {
            this.idleBones = {
                spine: humanoid.getNormalizedBoneNode('spine'),
                chest: humanoid.getNormalizedBoneNode('chest'),
                neck: humanoid.getNormalizedBoneNode('neck'),
                head: humanoid.getNormalizedBoneNode('head'),
                left: {
                    upper: humanoid.getNormalizedBoneNode('leftUpperArm'),
                    lower: humanoid.getNormalizedBoneNode('leftLowerArm'),
                    hand: humanoid.getNormalizedBoneNode('leftHand'),
                },
                right: {
                    upper: humanoid.getNormalizedBoneNode('rightUpperArm'),
                    lower: humanoid.getNormalizedBoneNode('rightLowerArm'),
                    hand: humanoid.getNormalizedBoneNode('rightHand'),
                },
            };
        }
        this.idleTime += dt;
        const t = this.idleTime;
        const b = this.idleBones;
        const mix = (a, c, w) => a + (c - a) * w;
        const smooth = (x) => {
            const v = Math.max(0, Math.min(1, x));
            return v * v * (3 - 2 * v);
        };

        // ── Deteccion de habla (RMS ~20Hz, hold para no parpadear) ──
        const mouth = Math.max(0, Math.min(1, Number(runtime.mouth) || 0));
        if (mouth > 0.05) {
            this.talking = true;
            this.mouthHoldMs = 250;
        } else if (this.mouthHoldMs > 0) {
            this.mouthHoldMs -= dt * 1000;
            this.talking = this.mouthHoldMs > 0;
        } else {
            this.talking = false;
        }
        const targetTalk = this.talking ? 1 : 0;
        this.talkWeight += (targetTalk - this.talkWeight) * Math.min(1, dt * 4);

        // ── Envolvente del gesto activo (entrada 0.35s, salida 0.5s) ──
        let g = 0;
        let gestureName = null;
        if (this.gesture) {
            this.gesture.t += dt;
            const { name, t: gt, dur } = this.gesture;
            if (gt >= dur) {
                this.gesture = null;
            } else {
                gestureName = name;
                g = Math.min(smooth(gt / 0.35), smooth((dur - gt) / 0.5));
            }
        }
        // Al gesticular un emote/saludo se atenua la capa de habla.
        const tw = this.talkWeight * (1 - g * 0.8);

        // ── Capa base: respiracion + balanceo ──
        let breath = Math.sin(t * 1.6) * 0.025;
        let spineX = 0;
        let spineZ = Math.sin(t * 0.45) * 0.015;
        let headX = Math.sin(t * 0.55) * 0.025 + Math.sin(t * 3.1) * 0.03 * tw;
        let headY = 0;
        let headZ = Math.sin(t * 0.38) * 0.015;

        // ── Capa de brazos: reposo + habla ──
        const armSway = Math.sin(t * 1.6) * 0.015;
        const arms = {};
        for (const side of ['left', 'right']) {
            const pose = this.armPose?.[side];
            const bones = b[side];
            if (!pose || !bones.upper) continue;
            const sign = pose.sign;
            const phase = side === 'left' ? 0 : Math.PI * 0.7;
            arms[side] = {
                sign,
                bones,
                upperZ: pose.base - sign * armSway * (1 - tw)
                    - sign * (0.22 + 0.06 * Math.sin(t * 2.3 + phase)) * tw,
                lowerY: sign * (0.55 + 0.28 * Math.sin(t * 2.3 + phase)) * tw,
                lowerZ: 0,
                handY: sign * 0.15 * Math.sin(t * 4.6 + phase) * tw,
                handZ: 0,
            };
        }

        // ── Capa de gesto one-shot ──
        if (gestureName && g > 0) {
            const L = arms.left;
            const R = arms.right;
            switch (gestureName) {
                case 'wave': {
                    // Saludo: brazo derecho arriba, antebrazo vertical, mano ondeando.
                    if (R) {
                        R.upperZ = mix(R.upperZ, this.armPose.right.base - R.sign * 1.7, g);
                        R.lowerZ = mix(0, -R.sign * 0.65, g);
                        R.handZ = Math.sin(t * 11) * 0.5 * g;
                    }
                    headZ += 0.06 * g;
                    break;
                }
                case 'happy': {
                    // Brazos en V hacia arriba + pecho abierto.
                    for (const a of [L, R].filter(Boolean)) {
                        a.upperZ = mix(a.upperZ, this.armPose[a === L ? 'left' : 'right'].base - a.sign * 0.95, g);
                        a.lowerZ = mix(a.lowerZ, -a.sign * 0.45, g);
                    }
                    breath += 0.05 * g * Math.sin(t * 6);
                    headX -= 0.08 * g;
                    break;
                }
                case 'sad': {
                    headX += 0.30 * g;
                    spineX += 0.10 * g;
                    for (const a of [L, R].filter(Boolean)) {
                        a.upperZ = mix(a.upperZ, this.armPose[a === L ? 'left' : 'right'].base + a.sign * 0.08, g);
                    }
                    break;
                }
                case 'angry': {
                    for (const a of [L, R].filter(Boolean)) {
                        a.upperZ = mix(a.upperZ, this.armPose[a === L ? 'left' : 'right'].base - a.sign * 0.25, g);
                        a.lowerY = mix(a.lowerY, a.sign * 0.75, g);
                    }
                    headY = Math.sin(t * 7) * 0.12 * g;
                    break;
                }
                case 'surprised': {
                    for (const a of [L, R].filter(Boolean)) {
                        a.upperZ = mix(a.upperZ, this.armPose[a === L ? 'left' : 'right'].base - a.sign * 1.1, g);
                        a.lowerZ = mix(a.lowerZ, -a.sign * 0.3, g);
                    }
                    headX -= 0.22 * g;
                    break;
                }
                case 'relaxed': {
                    breath = Math.sin(t * 0.9) * 0.05;
                    headZ += 0.10 * g;
                    spineZ += 0.02 * g * Math.sin(t * 0.6);
                    break;
                }
            }
        }

        // ── Aplicar ──
        if (b.chest) b.chest.rotation.x = breath;
        if (b.spine) {
            b.spine.rotation.x = spineX + (b.chest ? 0 : breath);
            b.spine.rotation.z = spineZ;
        }
        if (b.neck) b.neck.rotation.y = Math.sin(t * 0.32) * 0.04;
        if (b.head) {
            b.head.rotation.x = headX;
            b.head.rotation.y = headY;
            b.head.rotation.z = headZ;
        }
        for (const side of ['left', 'right']) {
            const a = arms[side];
            if (!a) continue;
            a.bones.upper.rotation.z = a.upperZ;
            if (a.bones.lower) {
                a.bones.lower.rotation.y = a.lowerY;
                a.bones.lower.rotation.z = a.lowerZ;
            }
            if (a.bones.hand) {
                a.bones.hand.rotation.y = a.handY;
                a.bones.hand.rotation.z = a.handZ;
            }
        }
    }

    async load(ctx) {
        const { THREE, scene, camera } = ctx;
        this.THREE = THREE;
        this.scene = scene;

        const baseUrl = `gmini-skin://${this.manifest.id}/`;

        const loader = new GLTFLoader();
        loader.register((parser) => new VRMLoaderPlugin(parser));

        const gltf = await loader.loadAsync(baseUrl + (this.manifest.model || 'model.vrm'));
        const vrm = gltf.userData.vrm;
        if (!vrm) {
            throw new Error('El archivo .vrm no contiene datos VRM validos');
        }

        VRMUtils.removeUnnecessaryVertices(gltf.scene);
        if (vrm.meta?.metaVersion === '0') {
            VRMUtils.rotateVRM0(vrm);
        }
        vrm.scene.traverse((obj) => { obj.frustumCulled = false; });

        this.vrm = vrm;
        scene.add(vrm.scene);

        const camCfg = this.manifest.camera || {};
        const pos = camCfg.position || [0, 1.3, 2.2];
        const target = camCfg.target || [0, 1.0, 0];
        camera.position.set(pos[0], pos[1], pos[2]);
        camera.lookAt(new THREE.Vector3(target[0], target[1], target[2]));

        this.mixer = new THREE.AnimationMixer(vrm.scene);

        const animations = this.manifest.animations || {};
        for (const [name, relPath] of Object.entries(animations)) {
            try {
                const clip = await loadMixamoAnimation(THREE, baseUrl + relPath, vrm);
                this.actions[name] = this.mixer.clipAction(clip);
            } catch (err) {
                console.warn(`[VrmSkin] No se pudo cargar animacion '${name}':`, err);
            }
        }

        if (Object.keys(this.actions).length) {
            this._playAction('idle');
        } else {
            // Sin animaciones: pose de reposo + idle procedural en update(),
            // con saludo de bienvenida al aparecer.
            this._applyRestPose();
            this._startGesture('wave', 2.8);
        }
    }

    _playAction(name, fadeSeconds = 0.4) {
        const next = this.actions[name] || this.actions.idle;
        if (!next || next === this.currentAction) return;
        next.reset().fadeIn(fadeSeconds).play();
        if (this.currentAction) this.currentAction.fadeOut(fadeSeconds);
        this.currentAction = next;
    }

    update(dt, runtime = {}) {
        if (!this.vrm) return;
        if (this.currentAction) {
            this.mixer.update(dt);
        } else {
            this._updateProceduralIdle(dt, runtime);
        }

        const expr = this.vrm.expressionManager;
        const mouth = Math.max(0, Math.min(1, Number(runtime.mouth) || 0));

        if (expr) {
            expr.setValue('aa', mouth);

            // Parpadeo periodico.
            this.blinkTimer += dt;
            if (this.blinkTimer >= this.nextBlink) {
                this.blinkTimer = 0;
                this.nextBlink = 3 + Math.random() * 2;
                expr.setValue('blink', 1);
                setTimeout(() => {
                    if (this.vrm?.expressionManager) this.vrm.expressionManager.setValue('blink', 0);
                }, 150);
            }

            // Decay de la expresion emocional hacia neutral.
            if (this.emotion !== 'neutral' && this.emotionIntensity > 0) {
                this.emotionIntensity = Math.max(0, this.emotionIntensity - dt / 4);
                expr.setValue(this.emotion, this.emotionIntensity);
                if (this.emotionIntensity === 0) this.emotion = 'neutral';
            }
        }

        this.vrm.update(dt);
    }

    setEmotion(name, intensity = 1) {
        const expr = this.vrm?.expressionManager;
        if (!expr || !EMOTIONS.includes(name)) return;
        for (const key of EMOTIONS) {
            expr.setValue(key, key === name ? intensity : 0);
        }
        this.emotion = name;
        this.emotionIntensity = Math.max(0, Math.min(1, intensity));

        // Emote corporal procedural (solo en modo sin animaciones Mixamo).
        if (!Object.keys(this.actions).length) {
            const durations = { happy: 2.4, sad: 2.8, angry: 2.0, surprised: 1.6, relaxed: 3.2 };
            this._startGesture(name, durations[name] || 2.0);
        }
    }

    setStatus(status) {
        this.status = status || 'idle';
        this._playAction(STATUS_TO_ACTION[this.status] || 'idle');
    }

    getBoundsPx() {
        return { width: 320, height: 420 };
    }

    dispose() {
        if (!this.vrm) return;
        this.scene.remove(this.vrm.scene);
        this.vrm.scene.traverse((obj) => {
            if (obj.geometry) obj.geometry.dispose();
            if (obj.material) {
                const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
                materials.forEach((m) => m.dispose());
            }
        });
        this.vrm = null;
        this.mixer = null;
        this.actions = {};
        this.currentAction = null;
    }
}
