/**
 * G-Mini Agent — Overlay de acciones (ventana transparente a pantalla completa).
 *
 * El proceso principal llama a estas funciones con executeJavaScript:
 * showClick, showScreenshot, showCursorAt y hideCursor. Antes vivían en una
 * URL data: que el navegador cortaba en el primer '#' del CSS, así que el
 * script nunca llegaba a existir y no se veía ningún efecto (B1).
 */
(function () {
    'use strict';

    const screenshotEl = document.getElementById('screenshot-overlay');
    const cursorBubble = document.getElementById('cursor-bubble');
    const CLICK_TYPES = ['click', 'double_click', 'right_click'];
    let hideTimer = null;
    let bubbleHideTimer = null;

    const toPixel = (value) => (Number.isFinite(Number(value)) ? Math.round(Number(value)) : 0);

    window.showCursorAt = (x, y) => {
        cursorBubble.style.left = `${toPixel(x)}px`;
        cursorBubble.style.top = `${toPixel(y)}px`;
        cursorBubble.classList.add('visible');
        clearTimeout(bubbleHideTimer);
        bubbleHideTimer = setTimeout(() => cursorBubble.classList.remove('visible'), 3000);
    };

    window.hideCursor = () => {
        cursorBubble.classList.remove('visible');
    };

    window.showClick = (x, y, type) => {
        const px = toPixel(x);
        const py = toPixel(y);
        const point = document.createElement('div');
        point.className = `click-point ${CLICK_TYPES.includes(type) ? type : 'click'}`;
        point.style.left = `${px}px`;
        point.style.top = `${py}px`;
        ['dot', 'ripple ripple-1', 'ripple ripple-2'].forEach((cls) => {
            const part = document.createElement('div');
            part.className = cls;
            point.appendChild(part);
        });
        const label = document.createElement('div');
        label.className = 'coord-label';
        label.textContent = `${px}, ${py}`;
        point.appendChild(label);
        document.body.appendChild(point);
        setTimeout(() => point.remove(), 1200);
        window.showCursorAt(px, py);
    };

    window.showScreenshot = () => {
        screenshotEl.classList.remove('active');
        void screenshotEl.offsetWidth; // reinicia las animaciones
        screenshotEl.classList.add('active');
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => screenshotEl.classList.remove('active'), 900);
    };
})();
