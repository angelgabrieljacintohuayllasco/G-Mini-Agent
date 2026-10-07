/**
 * G-Mini Agent — Autenticación del renderer contra el núcleo local.
 *
 * El núcleo exige un token de sesión en toda petición a /api (protección
 * contra CSRF y DNS rebinding). Este archivo se carga antes que el resto y
 * agrega la cabecera X-GMini-Token a cada fetch dirigido al backend, así
 * ninguna vista tiene que acordarse de hacerlo.
 */
(function () {
    const BACKEND_ORIGINS = ['http://127.0.0.1:8765', 'http://localhost:8765'];
    const TOKEN_HEADER = 'X-GMini-Token';
    let token = '';

    const ready = (async () => {
        try {
            if (window.gmini && typeof window.gmini.getSessionToken === 'function') {
                token = (await window.gmini.getSessionToken()) || '';
            }
        } catch (err) {
            console.error('No se pudo obtener el token de sesión del núcleo:', err);
        }
        return token;
    })();

    const isBackendUrl = (url) => BACKEND_ORIGINS.some((origin) => url.startsWith(origin));
    const nativeFetch = window.fetch.bind(window);

    window.fetch = async (input, init) => {
        let url = '';
        if (typeof input === 'string') url = input;
        else if (input instanceof URL) url = input.href;
        else if (input && typeof input.url === 'string') url = input.url;

        if (!isBackendUrl(url)) return nativeFetch(input, init);
        await ready;

        if (input instanceof Request) {
            const request = new Request(input, init);
            if (token) request.headers.set(TOKEN_HEADER, token);
            return nativeFetch(request);
        }
        const headers = new Headers((init && init.headers) || {});
        if (token) headers.set(TOKEN_HEADER, token);
        return nativeFetch(input, { ...(init || {}), headers });
    };

    window.gminiAuth = {
        ready,
        getToken: () => token,
        /** URL de un recurso del backend con el token como parámetro (para <img>, <audio>, descargas). */
        withToken: (url) => {
            if (!token || !isBackendUrl(url)) return url;
            const sep = url.includes('?') ? '&' : '?';
            return `${url}${sep}token=${encodeURIComponent(token)}`;
        },
    };
})();
