/**
 * G-Mini Agent — AudioWorklet de captura del micrófono.
 *
 * Convierte la entrada mono (a la frecuencia del AudioContext: 16 kHz) en
 * PCM16 y la entrega por el puerto en trozos de `chunkSize` muestras, con la
 * misma conversión que usaba el ScriptProcessorNode. Vive en su propio
 * archivo porque un worklet se carga como módulo (CSP script-src 'self').
 * No se incluye con <script>: lo carga mic-capture.js con addModule().
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        this.size = Number(options?.processorOptions?.chunkSize) || 2048;
        this.chunk = new Int16Array(this.size);
        this.filled = 0;
        this.running = true;
        this.port.onmessage = (event) => {
            if (event.data === 'stop') this.running = false;
        };
    }

    process(inputs) {
        if (!this.running) return false;
        const channel = inputs[0] && inputs[0][0];
        if (!channel) return true;
        for (let i = 0; i < channel.length; i += 1) {
            const s = Math.max(-1, Math.min(1, channel[i]));
            this.chunk[this.filled] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            this.filled += 1;
            if (this.filled === this.size) {
                // Se transfiere el búfer (sin copia) y se empieza uno nuevo.
                this.port.postMessage(this.chunk.buffer, [this.chunk.buffer]);
                this.chunk = new Int16Array(this.size);
                this.filled = 0;
            }
        }
        return true;
    }
}

registerProcessor('gmini-pcm-capture', PcmCaptureProcessor);
