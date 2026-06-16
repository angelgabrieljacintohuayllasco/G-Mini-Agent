import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { SkinBase } from './skinBase.js';

const DRACO_DECODER_PATH = './vendor/three/jsm/libs/draco/gltf/';

// Skin estatica de respaldo: GLB optimizado (gltf-transform) sin rig/morphs.
// Solo aplica balanceo procedural + pulso de escala al hablar.
export class GlbStaticSkin extends SkinBase {
    constructor(manifest = {}) {
        super();
        this.manifest = manifest;
        this.model = null;
        this.scene = null;
        this.elapsed = 0;
        this.baseScale = Number(manifest.scale) || 1;
    }

    async load(ctx) {
        const { THREE, scene, camera } = ctx;
        this.THREE = THREE;
        this.scene = scene;

        const dracoLoader = new DRACOLoader();
        dracoLoader.setDecoderPath(DRACO_DECODER_PATH);

        const loader = new GLTFLoader();
        loader.setDRACOLoader(dracoLoader);

        const baseUrl = `gmini-skin://${this.manifest.id}/`;
        const gltf = await loader.loadAsync(baseUrl + (this.manifest.model || 'model.glb'));
        this.model = gltf.scene;
        this.model.scale.setScalar(this.baseScale);

        // Centrar en X/Z y apoyar sobre y=0.
        const box = new THREE.Box3().setFromObject(this.model);
        const center = box.getCenter(new THREE.Vector3());
        this.model.position.x -= center.x;
        this.model.position.z -= center.z;
        this.model.position.y -= box.min.y;

        scene.add(this.model);

        const size = box.getSize(new THREE.Vector3());
        const camCfg = this.manifest.camera || {};
        const pos = camCfg.position || [0, size.y * 0.6, Math.max(size.length() * 0.7, 1.5)];
        const target = camCfg.target || [0, size.y * 0.5, 0];
        camera.position.set(pos[0], pos[1], pos[2]);
        camera.lookAt(new THREE.Vector3(target[0], target[1], target[2]));

        dracoLoader.dispose();
    }

    update(dt, runtime = {}) {
        if (!this.model) return;
        this.elapsed += dt;
        const mouth = Math.max(0, Math.min(1, Number(runtime.mouth) || 0));

        // Balanceo idle suave.
        this.model.rotation.y = Math.sin(this.elapsed * 0.4) * 0.08;

        // Pulso de escala al hablar.
        this.model.scale.setScalar(this.baseScale * (1 + mouth * 0.03));
    }

    setStatus(_status) {}

    setEmotion(_name, _intensity) {}

    getBoundsPx() {
        return { width: 320, height: 420 };
    }

    dispose() {
        if (!this.model) return;
        this.model.traverse((obj) => {
            if (obj.geometry) obj.geometry.dispose();
            if (obj.material) {
                const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
                materials.forEach((m) => m.dispose());
            }
        });
        this.scene.remove(this.model);
        this.model = null;
    }
}
