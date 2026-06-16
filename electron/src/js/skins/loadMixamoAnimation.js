import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { mixamoVRMRigMap } from './mixamoVRMRigMap.js';

// Carga un FBX de animacion de Mixamo ("Y Bot") y lo retargetea a un VRM
// Humanoid arbitrario via mixamoVRMRigMap. Patron estandar de la comunidad
// three-vrm: https://github.com/pixiv/three-vrm (load-mixamo-animation example).
export async function loadMixamoAnimation(THREE, url, vrm) {
    const loader = new FBXLoader();
    const asset = await loader.loadAsync(url);

    const clip = THREE.AnimationClip.findByName(asset.animations, 'mixamo.com');
    if (!clip) {
        throw new Error(`No se encontro un AnimationClip 'mixamo.com' en ${url}`);
    }

    const tracks = [];
    const restRotationInverse = new THREE.Quaternion();
    const parentRestWorldRotation = new THREE.Quaternion();
    const quatTmp = new THREE.Quaternion();
    const vecTmp = new THREE.Vector3();

    const motionHips = asset.getObjectByName('mixamorigHips');
    const motionHipsHeight = motionHips ? motionHips.position.y : 1;

    const vrmHipsNode = vrm.humanoid?.getNormalizedBoneNode('hips');
    const vrmHipsY = vrmHipsNode ? vrmHipsNode.getWorldPosition(vecTmp).y : motionHipsHeight;
    const vrmRootY = vrm.scene.getWorldPosition(vecTmp).y;
    const vrmHipsHeight = Math.abs(vrmHipsY - vrmRootY) || motionHipsHeight;
    const hipsPositionScale = motionHipsHeight ? vrmHipsHeight / motionHipsHeight : 1;

    const isVrm0 = vrm.meta?.metaVersion === '0';

    for (const track of clip.tracks) {
        const [mixamoRigName, propertyName] = track.name.split('.');
        const vrmBoneName = mixamoVRMRigMap[mixamoRigName];
        const mixamoRigNode = asset.getObjectByName(mixamoRigName);
        const vrmNode = vrmBoneName ? vrm.humanoid?.getNormalizedBoneNode(vrmBoneName) : null;

        if (!vrmNode || !mixamoRigNode) continue;
        const vrmNodeName = vrmNode.name;

        if (track instanceof THREE.QuaternionKeyframeTrack) {
            mixamoRigNode.getWorldQuaternion(restRotationInverse).invert();
            mixamoRigNode.parent.getWorldQuaternion(parentRestWorldRotation);

            const values = track.values.slice();
            for (let i = 0; i < values.length; i += 4) {
                quatTmp.fromArray(values, i);
                quatTmp.premultiply(parentRestWorldRotation).multiply(restRotationInverse);
                if (isVrm0) {
                    quatTmp.x *= -1;
                    quatTmp.w *= -1;
                }
                quatTmp.toArray(values, i);
            }
            tracks.push(new THREE.QuaternionKeyframeTrack(`${vrmNodeName}.${propertyName}`, track.times, values));
        } else if (track instanceof THREE.VectorKeyframeTrack) {
            const values = track.values.map((v, i) => {
                const scaled = v * hipsPositionScale;
                return isVrm0 && i % 3 !== 1 ? -scaled : scaled;
            });
            tracks.push(new THREE.VectorKeyframeTrack(`${vrmNodeName}.${propertyName}`, track.times, values));
        }
    }

    return new THREE.AnimationClip('vrmAnimation', clip.duration, tracks);
}
