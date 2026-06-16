import { SkinBase } from './skinBase.js';

const STATUS_COLORS = {
    idle: 0x4aa8ff,
    thinking: 0xffb84a,
    calling: 0xffb84a,
    responding: 0x6affc8,
    executing: 0xff6ad1,
};

const MAX_WAVES = 6;

// Skin "Pulso + ondas": esfera wireframe + red de nodos con rotacion lenta,
// pulsa con la amplitud de audio (mouth) y emite ondas de glow al hablar.
export class EnergyBallSkin extends SkinBase {
    constructor() {
        super();
        this.group = null;
        this.core = null;
        this.wireframe = null;
        this.points = null;
        this.waves = [];
        this.elapsed = 0;
        this.targetColor = STATUS_COLORS.idle;
        this.currentColor = STATUS_COLORS.idle;
        this.status = 'idle';
        this.waveTimer = 0;
    }

    async load(ctx) {
        const { THREE, scene, camera } = ctx;
        this.THREE = THREE;
        this.scene = scene;

        camera.fov = 48;
        camera.position.set(0, 0, 3.1);
        camera.updateProjectionMatrix();
        camera.lookAt(0, 0, 0);

        this.group = new THREE.Group();
        scene.add(this.group);

        const geometry = new THREE.IcosahedronGeometry(1, 2);

        const edges = new THREE.EdgesGeometry(geometry);
        this.wireframe = new THREE.LineSegments(
            edges,
            new THREE.LineBasicMaterial({
                color: this.currentColor,
                transparent: true,
                opacity: 0.55,
            })
        );
        this.group.add(this.wireframe);

        this.points = new THREE.Points(
            geometry,
            new THREE.PointsMaterial({
                color: this.currentColor,
                size: 0.045,
                transparent: true,
                opacity: 0.9,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
            })
        );
        this.group.add(this.points);

        this.core = new THREE.Mesh(
            new THREE.SphereGeometry(0.32, 24, 24),
            new THREE.MeshBasicMaterial({
                color: this.currentColor,
                transparent: true,
                opacity: 0.35,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
            })
        );
        this.group.add(this.core);
    }

    _spawnWave() {
        if (!this.THREE || this.waves.length >= MAX_WAVES) return;
        const THREE = this.THREE;
        const wave = new THREE.Mesh(
            new THREE.SphereGeometry(1, 24, 24),
            new THREE.MeshBasicMaterial({
                color: this.currentColor,
                transparent: true,
                opacity: 0.35,
                wireframe: true,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
            })
        );
        wave.scale.setScalar(0.9);
        wave.userData.life = 0;
        this.group.add(wave);
        this.waves.push(wave);
    }

    update(dt, runtime = {}) {
        if (!this.group) return;
        this.elapsed += dt;

        const mouth = Math.max(0, Math.min(1, Number(runtime.mouth) || 0));
        const speaking = mouth > 0.04 || runtime.status === 'responding';

        // Color: transicion suave hacia el color del estado actual.
        this.currentColor = lerpColor(this.currentColor, this.targetColor, Math.min(1, dt * 4));
        this.wireframe.material.color.setHex(this.currentColor);
        this.points.material.color.setHex(this.currentColor);
        this.core.material.color.setHex(this.currentColor);

        // Rotacion idle lenta + aceleracion ligera al pensar.
        const spinSpeed = runtime.status === 'thinking' || runtime.status === 'calling' ? 0.6 : 0.18;
        this.group.rotation.y += dt * spinSpeed;
        this.group.rotation.x = Math.sin(this.elapsed * 0.25) * 0.08;

        // Respiracion idle + pulso por amplitud de voz.
        const breathing = 1 + Math.sin(this.elapsed * 1.6) * 0.02;
        const pulse = 1 + mouth * 0.22;
        const scale = breathing * pulse;
        this.group.scale.setScalar(scale);

        this.core.material.opacity = 0.3 + mouth * 0.45;
        this.points.material.opacity = 0.7 + mouth * 0.3;

        // Ondas de glow mientras habla.
        if (speaking) {
            this.waveTimer -= dt;
            if (this.waveTimer <= 0) {
                this._spawnWave();
                this.waveTimer = 0.35 - mouth * 0.15;
            }
        } else {
            this.waveTimer = 0;
        }

        for (let i = this.waves.length - 1; i >= 0; i -= 1) {
            const wave = this.waves[i];
            wave.userData.life += dt;
            const t = wave.userData.life / 0.9;
            if (t >= 1) {
                this.group.remove(wave);
                wave.geometry.dispose();
                wave.material.dispose();
                this.waves.splice(i, 1);
                continue;
            }
            wave.scale.setScalar(0.9 + t * 0.2);
            wave.material.opacity = 0.35 * (1 - t);
            wave.material.color.setHex(this.currentColor);
        }
    }

    setSpeaking(level) {
        // El pulso ya se controla via runtime.mouth en update(); mantenido por compatibilidad.
        this._lastLevel = level;
    }

    setEmotion(_name, _intensity) {
        // La bola de energia no tiene expresiones faciales; reservado para skins VRM.
    }

    setStatus(status) {
        this.status = status || 'idle';
        this.targetColor = STATUS_COLORS[this.status] ?? STATUS_COLORS.idle;
    }

    getBoundsPx() {
        return { width: 320, height: 360 };
    }

    dispose() {
        if (!this.group) return;
        for (const wave of this.waves) {
            wave.geometry.dispose();
            wave.material.dispose();
        }
        this.waves = [];
        this.wireframe.geometry.dispose();
        this.wireframe.material.dispose();
        this.points.material.dispose();
        this.core.geometry.dispose();
        this.core.material.dispose();
        this.scene.remove(this.group);
        this.group = null;
    }
}

function lerpColor(fromHex, toHex, t) {
    const fr = (fromHex >> 16) & 0xff;
    const fg = (fromHex >> 8) & 0xff;
    const fb = fromHex & 0xff;
    const tr = (toHex >> 16) & 0xff;
    const tg = (toHex >> 8) & 0xff;
    const tb = toHex & 0xff;
    const r = Math.round(fr + (tr - fr) * t);
    const g = Math.round(fg + (tg - fg) * t);
    const b = Math.round(fb + (tb - fb) * t);
    return (r << 16) | (g << 8) | b;
}
