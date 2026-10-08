/**
 * G-Mini Agent — Captura del micrófono en PCM16 con AudioWorklet.
 *
 * La usan la voz en tiempo real (también en modo simulado) y el modo manos
 * libres. attach(ctx, source, {chunkSize, onChunk}) conecta la fuente a un
 * AudioWorkletNode que entrega Int16Array de chunkSize muestras (2048 = 128 ms
 * a 16 kHz) y devuelve {worklet, stop()}. Reemplaza al ScriptProcessorNode,
 * que Chromium da por obsoleto; solo si el worklet no carga se usa ese nodo
 * como respaldo, para que la voz no deje de funcionar.
 */
(function () {
    'use strict';

    const MODULE_URL = 'js/pcm-capture-worklet.js';
    const PROCESSOR = 'gmini-pcm-capture';
    const modules = new WeakMap();   // AudioContext -> addModule() en curso o hecho

    function loadModule(ctx) {
        let pending = modules.get(ctx);
        if (!pending) {
            pending = ctx.audioWorklet.addModule(MODULE_URL);
            modules.set(ctx, pending);
        }
        return pending;
    }

    function toPcm16(float32) {
        const pcm16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i += 1) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return pcm16;
    }

    function scriptProcessorFallback(ctx, source, chunkSize, onChunk) {
        const processor = ctx.createScriptProcessor(chunkSize, 1, 1);
        processor.onaudioprocess = (ev) => onChunk(toPcm16(ev.inputBuffer.getChannelData(0)));
        source.connect(processor);
        processor.connect(ctx.destination);
        return {
            worklet: false,
            stop() {
                processor.onaudioprocess = null;
                try { source.disconnect(processor); } catch (e) { /* ya desconectado */ }
                try { processor.disconnect(); } catch (e) { /* ya desconectado */ }
            },
        };
    }

    async function attach(ctx, source, { chunkSize = 2048, onChunk = () => {} } = {}) {
        try {
            if (!ctx.audioWorklet) throw new Error('AudioWorklet no disponible');
            await loadModule(ctx);
        } catch (err) {
            console.warn('[Audio] El worklet de captura no cargó; uso ScriptProcessorNode:', err?.message || err);
            return scriptProcessorFallback(ctx, source, chunkSize, onChunk);
        }
        const node = new AudioWorkletNode(ctx, PROCESSOR, {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            outputChannelCount: [1],
            channelCount: 1,
            channelCountMode: 'explicit',
            processorOptions: { chunkSize },
        });
        node.port.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) onChunk(new Int16Array(event.data));
        };
        source.connect(node);
        // Conectado al destino para que el grafo lo procese; su salida es silencio.
        node.connect(ctx.destination);
        let stopped = false;
        return {
            worklet: true,
            stop() {
                if (stopped) return;
                stopped = true;
                node.port.onmessage = null;
                try { node.port.postMessage('stop'); } catch (e) { /* puerto cerrado */ }
                try { source.disconnect(node); } catch (e) { /* ya desconectado */ }
                try { node.disconnect(); } catch (e) { /* ya desconectado */ }
                try { node.port.close(); } catch (e) { /* puerto cerrado */ }
            },
        };
    }

    /** PCM16 a base64 en bloques (más rápido que byte a byte para trozos de 4 KB). */
    function toBase64(int16) {
        const bytes = new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 0x2000) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x2000));
        }
        return btoa(binary);
    }

    window.gminiPcmCapture = { attach, toBase64 };
})();
