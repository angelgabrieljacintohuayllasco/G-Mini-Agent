// Interfaz comun que implementan todas las skins (energyBallSkin, vrmSkin, glbStaticSkin...).
// skinRuntime.js solo llama estos metodos, nunca toca Three.js directamente.
export class SkinBase {
    /**
     * @param {{THREE: object, scene: object, camera: object}} ctx
     */
    async load(ctx) {
        // Las subclases deben crear su contenido y agregarlo a ctx.scene.
    }

    /**
     * @param {number} dt segundos desde el ultimo frame
     * @param {{status: string, mouth: number, emotion: string, audioHintMs: number}} runtime
     */
    update(dt, runtime) {}

    /** @param {number} level 0..1 */
    setSpeaking(level) {}

    /** @param {string} name @param {number} intensity 0..1 */
    setEmotion(name, intensity) {}

    /** @param {string} status idle|thinking|responding|calling|executing */
    setStatus(status) {}

    /** @returns {{width: number, height: number}} tamano preferido en px del personaje, para anclar UI */
    getBoundsPx() {
        return { width: 320, height: 360 };
    }

    dispose() {}
}
