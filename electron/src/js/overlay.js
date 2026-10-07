/**
 * G-Mini Agent — Overlay de texto (ventana transparente y sin foco).
 * Antes era un <script> en línea; como archivo propio la página puede usar
 * una CSP sin 'unsafe-inline'.
 */
(function () {
    'use strict';

    const overlayText = document.getElementById('overlay-text');
    if (!overlayText || !window.gmini) return;

    window.gmini.onOverlayText((text) => {
        overlayText.textContent = String(text || '');
    });
})();
