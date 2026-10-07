/**
 * G-Mini Agent — Utilidades de DOM compartidas por las vistas.
 *
 * Se carga antes que el resto de scripts de la ventana principal. Todo vive
 * en window.gminiDom para no chocar con los globales de los demás archivos.
 */
(function () {
    'use strict';

    const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

    /** Escapa texto para HTML; también es seguro dentro de atributos (SEC3). */
    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
    }

    const SPRITE = 'assets/icons.svg';

    function iconHref(name) {
        return `${SPRITE}#i-${String(name).replace(/[^a-z0-9-]/g, '')}`;
    }

    /** Markup de un icono del sprite Lucide. `name` va sin el prefijo "i-". */
    function icon(name, extraClass = '') {
        const cls = extraClass ? `icon ${escapeHtml(extraClass)}` : 'icon';
        return `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="${iconHref(name)}"></use></svg>`;
    }

    /** Nodo SVG de un icono, para construir DOM sin innerHTML. */
    function iconEl(name, extraClass = '') {
        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('class', extraClass ? `icon ${extraClass}` : 'icon');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        const use = document.createElementNS(ns, 'use');
        use.setAttribute('href', iconHref(name));
        svg.appendChild(use);
        return svg;
    }

    const FOCUSABLE = [
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    /** Elementos enfocables y visibles dentro de `container`. */
    function focusables(container) {
        return Array.from(container.querySelectorAll(FOCUSABLE)).filter(
            (el) => el.getClientRects().length > 0 && !el.closest('[hidden], .hidden')
        );
    }

    /** Mantiene el foco dentro de `container` al tabular (diálogos modales). */
    function trapFocus(container, event) {
        if (event.key !== 'Tab') return;
        const list = focusables(container);
        if (!list.length) return;
        const first = list[0];
        const last = list[list.length - 1];
        const active = document.activeElement;
        if (!container.contains(active)) {
            event.preventDefault();
            first.focus();
        } else if (event.shiftKey && active === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && active === last) {
            event.preventDefault();
            first.focus();
        }
    }

    window.gminiDom = { escapeHtml, icon, iconEl, iconHref, focusables, trapFocus };
})();
