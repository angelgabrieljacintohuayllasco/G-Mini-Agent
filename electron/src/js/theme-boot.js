/**
 * G-Mini Agent — Theme boot
 *
 * Se carga en <head>, antes de cualquier CSS que pinte, para fijar el tema,
 * el acento, la densidad y el movimiento en <html> sin parpadeo (B30).
 *
 * Fuente de verdad en orden: localStorage (cache de arranque) -> argumentos de
 * proceso que el main inyecta en las ventanas secundarias (overlay/skin, que
 * pueden no tener localStorage propio) -> sistema.
 *
 * El guardado persistente real lo hace Settings vía PUT /api/config; este
 * archivo solo lee/escribe el cache local y aplica los atributos al vuelo.
 */
(function () {
    'use strict';

    var THEMES = ['dark', 'ocean', 'midnight', 'light', 'paper', 'contrast'];
    var ACCENTS = ['blue', 'violet', 'green', 'amber', 'rose', 'cyan'];
    var DENSITIES = ['comfortable', 'compact'];
    var MOTIONS = ['full', 'reduced', 'system'];

    function ls(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }
    function lsSet(key, value) {
        try { localStorage.setItem(key, value); } catch (e) { /* modo privado */ }
    }

    // El preload expone los valores iniciales que el main pasó por argv
    // (window.gmini.initialTheme). Puede no existir aún en este punto.
    function fromArgs() {
        try {
            var init = window.gmini && window.gmini.initialTheme;
            return init && typeof init === 'object' ? init : {};
        } catch (e) { return {}; }
    }

    function prefersDark() {
        try { return window.matchMedia('(prefers-color-scheme: dark)').matches; }
        catch (e) { return true; }
    }

    function resolveTheme(theme) {
        if (theme === 'system' || !theme) return prefersDark() ? 'dark' : 'light';
        return THEMES.indexOf(theme) >= 0 ? theme : 'dark';
    }

    function readPrefs() {
        var args = fromArgs();
        var theme = ls('gmini_theme') || args.theme || 'dark';
        var accent = ls('gmini_accent') || args.accent || 'blue';
        var density = ls('gmini_density') || args.density || 'comfortable';
        var motion = ls('gmini_motion') || args.motion || 'system';
        if (ACCENTS.indexOf(accent) < 0) accent = 'blue';
        if (DENSITIES.indexOf(density) < 0) density = 'comfortable';
        if (MOTIONS.indexOf(motion) < 0) motion = 'system';
        return { theme: theme, accent: accent, density: density, motion: motion };
    }

    function apply(prefs, opts) {
        var root = document.documentElement;
        var resolved = resolveTheme(prefs.theme);
        root.setAttribute('data-theme', resolved);
        root.setAttribute('data-accent', prefs.accent);
        root.setAttribute('data-density', prefs.density);
        if (prefs.motion === 'system') root.removeAttribute('data-motion');
        else root.setAttribute('data-motion', prefs.motion);
        // Guardar la elección "cruda" (incluido "system") para el próximo arranque.
        if (!opts || opts.persist !== false) {
            lsSet('gmini_theme', prefs.theme);
            lsSet('gmini_accent', prefs.accent);
            lsSet('gmini_density', prefs.density);
            lsSet('gmini_motion', prefs.motion);
        }
        current = prefs;
        return resolved;
    }

    var current = readPrefs();
    apply(current, { persist: false });

    // Reaccionar al cambio de tema del sistema cuando el usuario eligió "Sistema".
    try {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
            if (current.theme === 'system') apply(current, { persist: false });
        });
    } catch (e) { /* navegador viejo */ }

    // Las ventanas comparten localStorage (mismo origen file://): cuando
    // Configuración cambia el tema en la ventana principal, el overlay y el
    // avatar reciben el evento "storage" y se actualizan al vuelo.
    window.addEventListener('storage', function (e) {
        if (!e.key || e.key.indexOf('gmini_') !== 0) return;
        apply(readPrefs(), { persist: false });
    });

    // API pública para Settings y la paleta de comandos.
    window.gminiTheme = {
        THEMES: THEMES,
        ACCENTS: ACCENTS,
        get: function () { return Object.assign({}, current); },
        resolved: function () { return resolveTheme(current.theme); },
        /** Aplica una o varias preferencias. Devuelve el tema resuelto. */
        set: function (partial, opts) {
            var next = Object.assign({}, current, partial || {});
            var o = Object.assign({ animate: true }, opts || {});
            var root = document.documentElement;
            if (o.animate) {
                root.classList.add('theme-transition');
                window.setTimeout(function () { root.classList.remove('theme-transition'); }, 280);
            }
            return apply(next, o);
        },
    };
})();
