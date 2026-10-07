/**
 * G-Mini Agent — Chat Module
 * Renderizado de mensajes, streaming y Markdown básico.
 */

// Saneado del Markdown que devuelve marked: sin estilos ni formularios y
// enlaces siempre a una ventana nueva (el proceso principal la convierte en
// el navegador del sistema y nunca navega la app).
const MARKDOWN_SANITIZE = Object.freeze({
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select', 'iframe', 'object', 'embed'],
    FORBID_ATTR: ['style'],
    ADD_ATTR: ['target'],
});

class ChatManager {
    constructor() {
        this.messagesContainer = document.getElementById('messages');
        this.currentStreamingEl = null;
        this.streamingText = '';
        this.isStreaming = false;
        this.approvalCardEl = null;
        this._pendingScreenshotCard = null;
    }

    init() {
        this._lastScreenshotTime = 0;
        this.container = document.getElementById('chat-container');
        this.scrollPill = document.getElementById('btn-scroll-bottom');
        this._stickToBottom = true;
        this._typingEl = null;
        this._renderQueued = false;
        // Estado vacío original, para volver a mostrarlo al limpiar el chat.
        this._emptyStateHtml = this.messagesContainer.querySelector('.chat-empty')?.outerHTML || '';

        // B16: solo se sigue el final si el usuario ya estaba abajo.
        this.container?.addEventListener('scroll', () => {
            this._stickToBottom = this._isNearBottom();
            if (this._stickToBottom) this._setScrollPill(false);
        }, { passive: true });
        this.scrollPill?.addEventListener('click', () => this._scrollToBottom(true));
        this.messagesContainer.addEventListener('click', (e) => this._onMessagesClick(e));

        if (window.marked) window.marked.use({ gfm: true, breaks: true });
        if (window.DOMPurify && !ChatManager._sanitizerHooked) {
            window.DOMPurify.addHook('afterSanitizeAttributes', (node) => {
                if (node.tagName === 'A' && node.hasAttribute('href')) {
                    node.setAttribute('target', '_blank');
                    node.setAttribute('rel', 'noopener noreferrer');
                }
            });
            ChatManager._sanitizerHooked = true;
        }
    }

    /**
     * Añade un mensaje del usuario al chat.
     */
    addUserMessage(text) {
        this.hideTyping();
        const el = this._createMessageEl('user-message');
        el.textContent = String(text || '');
        this.messagesContainer.appendChild(el);
        // Lo que escribe el usuario siempre lleva la vista al final.
        this._scrollToBottom(true);
    }

    addSystemMessage(text) {
        const el = this._createMessageEl('system-message');
        el.innerHTML = this._renderMarkdown(String(text || ''));
        this.messagesContainer.appendChild(el);
        this._scrollToBottom();
    }

    /**
     * Añade un screenshot inline al chat con throttle de 3 segundos.
     */
    addScreenshot(base64Image, caption = '') {
        if (!base64Image || base64Image.length < 100) return;

        const now = Date.now();
        if (!caption && now - this._lastScreenshotTime < 500) return;
        if (!caption) this._lastScreenshotTime = now;

        const isGenerated = caption === 'generated_image';
        const el = this._createMessageEl(isGenerated ? 'generated-image-message' : 'screenshot-message');

        // Skeleton loader while image loads
        const skeleton = document.createElement('div');
        skeleton.className = 'screenshot-skeleton';
        skeleton.innerHTML = '<div class="screenshot-skeleton-shimmer"></div>';
        el.appendChild(skeleton);

        const img = document.createElement('img');
        img.style.display = 'none';
        // Auto-detect MIME from base64 header bytes
        let src;
        if (base64Image.startsWith('data:')) {
            src = base64Image;
        } else if (base64Image.startsWith('/9j/')) {
            src = `data:image/jpeg;base64,${base64Image}`;
        } else if (base64Image.startsWith('iVBOR')) {
            src = `data:image/png;base64,${base64Image}`;
        } else {
            src = `data:image/jpeg;base64,${base64Image}`;
        }
        img.src = src;
        img.alt = isGenerated ? 'Imagen generada por IA' : 'Captura de pantalla';
        img.loading = 'lazy';

        let retryCount = 0;
        img.addEventListener('load', () => {
            skeleton.remove();
            img.style.display = '';
            img.classList.add('screenshot-loaded');
        });
        img.addEventListener('click', () => this._showScreenshotModal(img.src));
        img.addEventListener('error', () => {
            retryCount++;
            // Try PNG if JPEG failed, or vice versa
            if (retryCount === 1) {
                img.src = `data:image/png;base64,${base64Image}`;
                return;
            }
            if (retryCount === 2) {
                img.src = `data:image/jpeg;base64,${base64Image}`;
                return;
            }
            // All retries failed
            skeleton.remove();
            img.style.display = 'none';
            const errDiv = document.createElement('div');
            errDiv.className = 'screenshot-error-msg';
            errDiv.innerHTML = `${window.gminiDom.icon('camera-off')} Captura no disponible`;
            el.appendChild(errDiv);
        });
        el.appendChild(img);
        if (isGenerated) {
            const label = document.createElement('div');
            label.className = 'generated-image-label';
            label.textContent = 'Imagen generada con IA';
            el.appendChild(label);
        }
        this.messagesContainer.appendChild(el);
        this._scrollToBottom();

        // Also attach to pending screenshot action card if exists
        if (!isGenerated && this._pendingScreenshotCard) {
            this._attachScreenshotToCard(this._pendingScreenshotCard, img.src);
            this._pendingScreenshotCard = null;
        }
    }

    /**
     * Añade un reproductor multimedia inline (imagen, video o audio) con
     * barra de herramientas: zoom/pantalla completa + descarga a carpeta.
     */
    addMediaPlayer(type, url, filename = '') {
        if (!url) return;
        const el = this._createMessageEl('generated-media-message');

        if (type === 'image') {
            const img = document.createElement('img');
            img.src = url;
            img.alt = filename || 'Imagen generada';
            img.className = 'generated-media-img';
            img.addEventListener('click', () => this._showMediaViewer(url, filename));
            img.addEventListener('load', () => img.classList.add('screenshot-loaded'));
            img.addEventListener('error', () => {
                img.style.display = 'none';
                const errDiv = document.createElement('div');
                errDiv.className = 'screenshot-error-msg';
                errDiv.textContent = 'No se pudo cargar la imagen';
                el.insertBefore(errDiv, el.firstChild);
            });
            el.appendChild(img);
            el.appendChild(this._buildMediaToolbar({
                label: filename || 'Imagen generada con IA',
                url, filename,
                onZoom: () => this._showMediaViewer(url, filename),
                zoomIcon: 'zoom', zoomTitle: 'Ampliar / Zoom',
            }));
        } else if (type === 'video') {
            const video = document.createElement('video');
            video.src = url;
            video.controls = true;
            video.preload = 'metadata';
            video.className = 'generated-media-video';
            video.addEventListener('error', () => {
                video.style.display = 'none';
                const errDiv = document.createElement('div');
                errDiv.className = 'screenshot-error-msg';
                errDiv.textContent = 'No se pudo cargar el video';
                el.insertBefore(errDiv, el.firstChild);
            });
            el.appendChild(video);
            el.appendChild(this._buildMediaToolbar({
                label: filename || 'Video generado con IA',
                url, filename,
                onZoom: () => { if (video.requestFullscreen) video.requestFullscreen(); },
                zoomIcon: 'fullscreen', zoomTitle: 'Pantalla completa',
            }));
        } else if (type === 'audio') {
            const audio = document.createElement('audio');
            audio.src = url;
            audio.controls = true;
            audio.preload = 'metadata';
            audio.className = 'generated-media-audio';
            audio.addEventListener('error', () => {
                audio.style.display = 'none';
                const errDiv = document.createElement('div');
                errDiv.className = 'screenshot-error-msg';
                errDiv.textContent = 'No se pudo cargar el audio';
                el.insertBefore(errDiv, el.firstChild);
            });
            el.appendChild(audio);
            el.appendChild(this._buildMediaToolbar({
                label: filename || 'Audio generado con IA',
                url, filename,
            }));
        }

        this.messagesContainer.appendChild(el);
        this._scrollToBottom();
    }

    /**
     * Barra de herramientas bajo un medio: etiqueta + (zoom/fullscreen) + descarga.
     */
    _buildMediaToolbar({ label, url, filename, onZoom = null, zoomIcon = 'zoom', zoomTitle = 'Ampliar' }) {
        const bar = document.createElement('div');
        bar.className = 'media-toolbar';

        const lbl = document.createElement('span');
        lbl.className = 'media-toolbar-label';
        lbl.textContent = label || '';
        bar.appendChild(lbl);

        const actions = document.createElement('div');
        actions.className = 'media-toolbar-actions';

        if (typeof onZoom === 'function') {
            const zoomBtn = this._mediaButton(zoomIcon, zoomTitle);
            zoomBtn.addEventListener('click', onZoom);
            actions.appendChild(zoomBtn);
        }

        const dlBtn = this._mediaButton('download', 'Descargar / Guardar como…');
        dlBtn.addEventListener('click', () => this._downloadMedia(url, filename, dlBtn));
        actions.appendChild(dlBtn);

        bar.appendChild(actions);
        return bar;
    }

    _mediaButton(icon, title) {
        const btn = document.createElement('button');
        btn.className = 'media-btn';
        btn.title = title;
        btn.setAttribute('aria-label', title);
        btn.innerHTML = this._mediaIcon(icon);
        return btn;
    }

    _mediaIcon(name) {
        const map = {
            download: 'download',
            zoom: 'zoom-in',
            'zoom-in': 'zoom-in',
            'zoom-out': 'zoom-out',
            fullscreen: 'maximize-2',
            reset: 'rotate-ccw',
            close: 'x',
        };
        return window.gminiDom.icon(map[name] || 'circle');
    }

    /**
     * Descarga un medio a una carpeta elegida por el usuario (dialogo nativo via
     * Electron). Fallback a <a download> si no esta el bridge. Soporta data: URIs.
     */
    async _downloadMedia(url, filename, btn) {
        try {
            if (window.gmini && typeof window.gmini.saveMediaAs === 'function') {
                if (btn) btn.classList.add('media-btn-busy');
                const res = await window.gmini.saveMediaAs(url, filename || '');
                if (btn) btn.classList.remove('media-btn-busy');
                if (res && res.ok) {
                    this._toast(`Guardado en: ${res.path}`);
                } else if (res && res.canceled) {
                    /* usuario cancelo */
                } else {
                    this._toast(`No se pudo guardar${res && res.error ? ': ' + res.error : ''}`, true);
                }
                return;
            }
            // Fallback navegador
            const a = document.createElement('a');
            a.href = url;
            a.download = filename || 'media';
            document.body.appendChild(a);
            a.click();
            a.remove();
        } catch (e) {
            if (btn) btn.classList.remove('media-btn-busy');
            this._toast('Error al descargar: ' + ((e && e.message) || e), true);
        }
    }

    /**
     * Visor de imagen ampliada con zoom (botones + rueda + arrastre) y descarga.
     */
    _showMediaViewer(src, filename = '') {
        this._closeViewer?.();
        const returnFocus = document.activeElement;

        const overlay = document.createElement('div');
        overlay.id = 'screenshot-modal';
        overlay.className = 'screenshot-modal-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', filename || 'Visor de imagen');

        let onKey = null;
        const close = () => {
            overlay.remove();
            if (onKey) document.removeEventListener('keydown', onKey);
            this._closeViewer = null;
            if (returnFocus && document.contains(returnFocus) && typeof returnFocus.focus === 'function') {
                returnFocus.focus();
            }
        };
        this._closeViewer = close;

        let scale = 1, tx = 0, ty = 0, dragging = false, sx = 0, sy = 0;

        const stage = document.createElement('div');
        stage.className = 'media-viewer-stage';

        const img = document.createElement('img');
        img.src = src;
        img.alt = filename || 'Imagen ampliada';
        img.className = 'media-viewer-img';
        stage.appendChild(img);

        const apply = () => { img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`; };
        const setScale = (s) => {
            scale = Math.min(8, Math.max(0.2, s));
            if (scale <= 1.001) { scale = 1; tx = 0; ty = 0; }
            img.style.cursor = scale > 1 ? 'grab' : 'zoom-out';
            apply();
        };

        const bar = document.createElement('div');
        bar.className = 'media-viewer-toolbar';
        const mk = (icon, title, fn) => {
            const b = this._mediaButton(icon, title);
            b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
            return b;
        };
        bar.appendChild(mk('zoom-in', 'Acercar', () => setScale(scale * 1.25)));
        bar.appendChild(mk('zoom-out', 'Alejar', () => setScale(scale / 1.25)));
        bar.appendChild(mk('reset', 'Restablecer', () => setScale(1)));
        bar.appendChild(mk('download', 'Descargar / Guardar como…', () => this._downloadMedia(src, filename)));
        bar.appendChild(mk('close', 'Cerrar (Esc)', () => close()));

        stage.addEventListener('wheel', (e) => {
            e.preventDefault();
            setScale(scale * (e.deltaY < 0 ? 1.12 : 0.89));
        }, { passive: false });
        img.addEventListener('mousedown', (e) => {
            if (scale <= 1) return;
            dragging = true; sx = e.clientX - tx; sy = e.clientY - ty;
            img.style.cursor = 'grabbing';
            e.preventDefault();
        });
        overlay.addEventListener('mousemove', (e) => {
            if (!dragging) return;
            tx = e.clientX - sx; ty = e.clientY - sy; apply();
        });
        overlay.addEventListener('mouseup', () => {
            dragging = false;
            if (scale > 1) img.style.cursor = 'grab';
        });
        img.addEventListener('dblclick', (e) => { e.stopPropagation(); setScale(scale > 1 ? 1 : 2); });
        overlay.addEventListener('click', (e) => { if (e.target === overlay || e.target === stage) close(); });
        onKey = (e) => {
            if (e.key === 'Escape') { e.preventDefault(); close(); }
            else if (e.key === 'Tab') window.gminiDom.trapFocus(overlay, e);
            else if (e.key === '+' || e.key === '=') setScale(scale * 1.25);
            else if (e.key === '-') setScale(scale / 1.25);
            else if (e.key === '0') setScale(1);
        };
        document.addEventListener('keydown', onKey);

        overlay.appendChild(bar);
        overlay.appendChild(stage);
        document.body.appendChild(overlay);
        bar.lastElementChild?.focus();
    }

    /** Alias retro-compatible: capturas de pantalla usan el mismo visor con zoom. */
    _showScreenshotModal(src, filename = '') {
        this._showMediaViewer(src, filename);
    }

    /** Notificacion efimera (toast) para feedback de descargas. */
    _toast(message, isError = false) {
        let host = document.getElementById('gmini-toast-host');
        if (!host) {
            host = document.createElement('div');
            host.id = 'gmini-toast-host';
            document.body.appendChild(host);
        }
        const t = document.createElement('div');
        t.className = 'gmini-toast' + (isError ? ' gmini-toast-error' : '');
        t.textContent = message;
        host.appendChild(t);
        requestAnimationFrame(() => t.classList.add('gmini-toast-show'));
        setTimeout(() => {
            t.classList.remove('gmini-toast-show');
            setTimeout(() => t.remove(), 300);
        }, 3600);
    }

    /**
     * Maneja un chunk de respuesta streaming del agente.
     */
    handleAgentMessage(data) {
        const { text, type, done } = data;

        if (type === 'error') {
            this._addErrorMessage(text);
            this.finishStreaming();
            return;
        }

        // Action summaries go to a collapsed system message, not streaming text
        if (type === 'action' || type === 'system' || type === 'warning') {
            if (this.isStreaming) this.finishStreaming();
            const cssClass = type === 'warning' ? 'warning-message'
                : type === 'action' ? 'system-message action-result'
                : 'system-message';
            const el = this._createMessageEl(cssClass);
            el.innerHTML = this._renderMarkdown(String(text || ''));
            this.messagesContainer.appendChild(el);
            this._scrollToBottom();
            return;
        }

        if (done) {
            this.finishStreaming();
            return;
        }

        if (!this.isStreaming) {
            this.startStreaming();
        }

        // Append text chunk
        this.streamingText += text;
        this._updateStreamingContent();
    }

    /**
     * Inicia un nuevo mensaje streaming.
     */
    startStreaming() {
        this.hideTyping();
        this.isStreaming = true;
        this.streamingText = '';
        this.currentStreamingEl = this._createMessageEl('assistant-message streaming-cursor');
        this.messagesContainer.appendChild(this.currentStreamingEl);
        // Los lectores de pantalla esperan a que termine en vez de leer cada fragmento.
        this.container?.setAttribute('aria-busy', 'true');
    }

    /**
     * Finaliza el streaming actual.
     */
    finishStreaming() {
        if (this.currentStreamingEl) {
            this.currentStreamingEl.classList.remove('streaming-cursor');
            const cleanText = this._stripActionLines(this.streamingText);
            if (!cleanText) {
                this.currentStreamingEl.remove();
            } else {
                this.currentStreamingEl.innerHTML = this._renderMarkdown(cleanText);
            }
        }
        this.currentStreamingEl = null;
        this.streamingText = '';
        this.isStreaming = false;
        this.container?.setAttribute('aria-busy', 'false');
        this._scrollToBottom();
    }

    /**
     * Strips [ACTION:...] lines from text to avoid duplication with action cards.
     */
    _stripActionLines(text) {
        return text
            .split('\n')
            .filter(line => !line.trim().match(/^\[ACTION:[^\]]+\]$/))
            .join('\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    /**
     * Actualiza el contenido del mensaje streaming.
     */
    _updateStreamingContent() {
        // B16: como mucho un render por fotograma aunque lleguen muchos fragmentos.
        if (this._renderQueued) return;
        this._renderQueued = true;
        requestAnimationFrame(() => {
            this._renderQueued = false;
            if (!this.currentStreamingEl) return;
            const cleanText = this._stripActionLines(this.streamingText);
            if (!cleanText) {
                this.currentStreamingEl.style.display = 'none';
            } else {
                this.currentStreamingEl.style.display = '';
                this.currentStreamingEl.innerHTML = this._renderMarkdown(cleanText);
            }
            this._scrollToBottom();
        });
    }

    /** Tres puntos animados mientras el agente piensa y aún no hay texto. */
    showTyping() {
        if (this.isStreaming || this._typingEl) return;
        const el = document.createElement('div');
        el.className = 'typing-indicator';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-label', 'El agente está pensando');
        el.innerHTML = '<span></span><span></span><span></span>';
        this._typingEl = el;
        this.messagesContainer.appendChild(el);
        this._scrollToBottom();
    }

    hideTyping() {
        if (!this._typingEl) return;
        this._typingEl.remove();
        this._typingEl = null;
    }

    _addErrorMessage(text) {
        this.hideTyping();
        const el = this._createMessageEl('error-message');
        el.textContent = text;
        this.messagesContainer.appendChild(el);
        this._scrollToBottom();
    }

    _createMessageEl(className) {
        const div = document.createElement('div');
        div.className = `message ${className}`;
        return div;
    }

    _isNearBottom() {
        const c = this.container;
        return !c || c.scrollHeight - c.scrollTop - c.clientHeight < 48;
    }

    _setScrollPill(visible) {
        if (this.scrollPill) this.scrollPill.hidden = !visible;
    }

    /**
     * Baja al final solo si el usuario ya estaba ahí (o si se fuerza). Si está
     * leyendo más arriba, aparece la pastilla "Ir al final" en vez de saltar.
     */
    _scrollToBottom(force = false) {
        const container = this.container || document.getElementById('chat-container');
        if (!container) return;
        if (!force && !this._stickToBottom) {
            this._setScrollPill(true);
            return;
        }
        requestAnimationFrame(() => {
            container.scrollTop = container.scrollHeight;
            this._stickToBottom = true;
            this._setScrollPill(false);
        });
    }

    /**
     * Renderizado básico de Markdown.
     */
    /**
     * Markdown real (marked, GFM) saneado con DOMPurify (B17). El código queda
     * protegido de las reglas de negrita/cursiva y cada bloque lleva su barra
     * con lenguaje y botón de copiar. Sin las librerías, cae a texto escapado.
     */
    _renderMarkdown(text) {
        if (!text) return '';
        const source = String(text);
        if (!window.marked || !window.DOMPurify) {
            return this._escapeHtml(source).replace(/\n/g, '<br>');
        }
        const clean = window.DOMPurify.sanitize(window.marked.parse(source), MARKDOWN_SANITIZE);
        if (!clean.includes('<pre')) return clean;
        // <template> es inerte: decorar aquí no ejecuta ni carga nada.
        const tpl = document.createElement('template');
        tpl.innerHTML = clean;
        tpl.content.querySelectorAll('pre').forEach((pre) => this._wrapCodeBlock(pre));
        return tpl.innerHTML;
    }

    _wrapCodeBlock(pre) {
        const code = pre.querySelector('code');
        const langClass = Array.from(code?.classList || []).find((c) => c.startsWith('language-'));
        const block = document.createElement('div');
        block.className = 'code-block';
        const header = document.createElement('div');
        header.className = 'code-block-header';
        const lang = document.createElement('span');
        lang.className = 'code-block-lang';
        lang.textContent = langClass ? langClass.slice('language-'.length) : 'texto';
        const copy = document.createElement('button');
        copy.type = 'button';
        copy.className = 'code-copy-btn';
        copy.dataset.copyCode = '';
        copy.setAttribute('aria-label', 'Copiar código');
        copy.innerHTML = `${window.gminiDom.icon('copy')}<span>Copiar</span>`;
        header.append(lang, copy);
        pre.replaceWith(block);
        block.append(header, pre);
    }

    _onMessagesClick(e) {
        const copyBtn = e.target.closest('[data-copy-code]');
        if (!copyBtn) return;
        const code = copyBtn.closest('.code-block')?.querySelector('pre')?.innerText || '';
        this._copyText(code).then((ok) => {
            copyBtn.classList.toggle('is-copied', ok);
            copyBtn.innerHTML = `${window.gminiDom.icon(ok ? 'copy-check' : 'circle-x')}<span>${ok ? 'Copiado' : 'Sin acceso'}</span>`;
            clearTimeout(copyBtn._resetTimer);
            copyBtn._resetTimer = setTimeout(() => {
                copyBtn.classList.remove('is-copied');
                copyBtn.innerHTML = `${window.gminiDom.icon('copy')}<span>Copiar</span>`;
            }, 1600);
        });
    }

    async _copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (_) {
            // Respaldo: selección temporal + execCommand.
            const area = document.createElement('textarea');
            area.value = text;
            area.setAttribute('readonly', '');
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            let ok = false;
            try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
            area.remove();
            return ok;
        }
    }

    _escapeHtml(text) {
        // Escapa también comillas: es seguro dentro de atributos (SEC3).
        return window.gminiDom.escapeHtml(text);
    }

    clear() {
        this.hideTyping();
        this.messagesContainer.innerHTML = this._emptyStateHtml || '';
        this.finishStreaming();
        this.approvalCardEl = null;
        this._stickToBottom = true;
        this._setScrollPill(false);
        window.gminiIdentity?.refreshNameSlots?.();
    }

    // ── Action activity cards ────────────────────────

    /**
     * Muestra una tarjeta de actividad en el chat indicando qué herramienta se ejecutó.
     * @param {string} type - Nombre de la herramienta (click, type, screenshot, etc.)
     * @param {object} params - Parámetros de la herramienta
     * @returns {HTMLElement} El elemento de la card para poder actualizarlo con el resultado
     */
    addActionCard(type, params) {
        this.hideTyping();
        const el = this._createMessageEl('action-message');
        const icon = this._getActionIcon(type, params);
        const label = this._getActionLabel(type, params);
        const category = this._getActionCategory(type);

        el.dataset.actionCategory = category;
        el.innerHTML = `
            <div class="action-header">
                <span class="action-icon">${icon}</span>
                <span class="action-label">${this._escapeHtml(label)}</span>
                <span class="action-status action-running">
                    <span class="action-spinner"></span>
                    ejecutando
                </span>
            </div>
            <div class="action-detail">${this._formatActionParams(type, params)}</div>
            <div class="action-progress-bar"><div class="action-progress-fill"></div></div>
        `;

        // Timer: update elapsed time. Safety cap (MAX_ACTION_SECONDS): si por
        // cualquier motivo no llega el agent:action_result, el contador NO debe
        // correr para siempre — se detiene solo y marca timeout.
        const MAX_ACTION_SECONDS = 180;
        const startTime = Date.now();
        const statusEl = el.querySelector('.action-status');
        el._actionTimer = setInterval(() => {
            const elapsed = Math.round((Date.now() - startTime) / 1000);
            if (!statusEl || !statusEl.classList.contains('action-running')) return;
            if (elapsed >= MAX_ACTION_SECONDS) {
                clearInterval(el._actionTimer);
                el._actionTimer = null;
                statusEl.innerHTML = `${elapsed}s`;
                statusEl.classList.remove('action-running');
                statusEl.classList.add('action-timeout');
                return;
            }
            if (elapsed >= 2) {
                statusEl.innerHTML = `<span class="action-spinner"></span>${elapsed}s`;
            }
        }, 1000);

        // Track screenshot action cards for thumbnail attachment
        if (type === 'screenshot' || type === 'browser_screenshot' || type === 'adb_screenshot') {
            this._pendingScreenshotCard = el;
        }

        this.messagesContainer.appendChild(el);
        this._scrollToBottom();
        return el;
    }

    /**
     * Attaches a screenshot thumbnail inside an action card.
     */
    _attachScreenshotToCard(cardEl, imgSrc) {
        if (!cardEl) return;
        const existing = cardEl.querySelector('.action-screenshot-thumb');
        if (existing) return;
        const thumb = document.createElement('img');
        thumb.className = 'action-screenshot-thumb';
        thumb.src = imgSrc;
        thumb.alt = 'Captura';
        thumb.addEventListener('click', (e) => {
            e.stopPropagation();
            this._showScreenshotModal(imgSrc);
        });
        cardEl.appendChild(thumb);
    }

    /**
     * Returns the category of an action for visual styling.
     */
    _getActionCategory(type) {
        if (['click', 'double_click', 'right_click', 'type', 'focus_type', 'press', 'hotkey', 'scroll', 'move', 'drag'].includes(type)) return 'interaction';
        if (['screenshot', 'browser_screenshot', 'adb_screenshot', 'screen_read_text', 'screen_preview_start'].includes(type)) return 'vision';
        if (type.startsWith('browser_')) return 'browser';
        if (['terminal_run'].includes(type)) return 'terminal';
        if (['task_complete'].includes(type)) return 'complete';
        if (['generate_image', 'generate_video', 'generate_music'].includes(type)) return 'creative';
        if (type.startsWith('file_')) return 'file';
        if (['wait'].includes(type)) return 'wait';
        return 'system';
    }

    /**
     * Actualiza una tarjeta de acción con su resultado.
     * @param {HTMLElement} cardEl - Elemento de la card devuelto por addActionCard
     * @param {boolean} success - Si la acción fue exitosa
     * @param {string} resultText - Texto del resultado
     */
    updateActionCard(cardEl, success, resultText, durationMs) {
        if (!cardEl) return;
        // Stop progress timer
        if (cardEl._actionTimer) {
            clearInterval(cardEl._actionTimer);
            cardEl._actionTimer = null;
        }

        // Complete progress bar animation
        const progressFill = cardEl.querySelector('.action-progress-fill');
        if (progressFill) {
            progressFill.style.width = '100%';
            progressFill.style.background = success
                ? 'var(--success)'
                : 'var(--error)';
            setTimeout(() => {
                const bar = cardEl.querySelector('.action-progress-bar');
                if (!bar) return;
                bar.style.opacity = '0';
                // Al terminar la barra deja de ocupar espacio en la tarjeta.
                setTimeout(() => bar.remove(), 400);
            }, 600);
        }

        const statusEl = cardEl.querySelector('.action-status');
        if (statusEl) {
            const checkSvg = window.gminiDom.icon('check');
            const xSvg = window.gminiDom.icon('x');
            // Duracion exacta (del backend) — se guarda visible en la tarjeta.
            let durText = '';
            const ms = Number(durationMs);
            if (Number.isFinite(ms) && ms > 0) {
                durText = ms >= 1000 ? ` · ${(ms / 1000).toFixed(1)}s` : ` · ${Math.round(ms)}ms`;
            }
            const durSpan = durText ? `<span class="action-duration">${durText}</span>` : '';
            statusEl.innerHTML = success ? `${checkSvg} OK${durSpan}` : `${xSvg} ERROR${durSpan}`;
            statusEl.className = `action-status ${success ? 'action-ok' : 'action-fail'}`;
        }

        // Update card border color on completion
        cardEl.classList.add(success ? 'action-completed' : 'action-failed');

        if (resultText) {
            let detailEl = cardEl.querySelector('.action-result');
            if (!detailEl) {
                detailEl = document.createElement('div');
                detailEl.className = 'action-result';
                cardEl.appendChild(detailEl);
            }
            const formatted = this._formatMediaResult(resultText);
            const maxLen = 300;
            const truncated = formatted.length > maxLen ? formatted.slice(0, maxLen) + '…' : formatted;
            detailEl.textContent = truncated;
            if (!success) detailEl.classList.add('action-result-error');
        }
        this._scrollToBottom();
    }

    /**
     * Formatea resultados de generación multimedia para mostrar de forma legible.
     * Convierte dicts crudos de Python en texto limpio.
     */
    _formatMediaResult(text) {
        if (!text) return '';
        // Detectar dicts de Python serializados: {'success': True, 'model': ...}
        const dictMatch = text.match(/^\{['\"](?:success|model|message|count|files)['\"]:/);
        if (!dictMatch) return text;
        try {
            // Convertir single quotes de Python a double quotes para parsear
            const jsonStr = text
                .replace(/'/g, '"')
                .replace(/\bTrue\b/g, 'true')
                .replace(/\bFalse\b/g, 'false')
                .replace(/\bNone\b/g, 'null');
            const data = JSON.parse(jsonStr);
            const parts = [];
            if (data.model) parts.push(`Modelo: ${data.model}`);
            if (data.message) parts.push(data.message);
            if (data.count) parts.push(`Archivos: ${data.count}`);
            if (Array.isArray(data.files)) {
                for (const f of data.files) {
                    if (f.filename) parts.push(f.filename);
                    else if (f.path) parts.push(f.path.split(/[/\\]/).pop());
                }
            }
            if (data.lyrics) parts.push(`Letra: ${data.lyrics.slice(0, 200)}`);
            return parts.length > 0 ? parts.join(' | ') : text;
        } catch {
            return text;
        }
    }

    /**
     * Mapea una tool de MCPControl a un "tipo" de icono ya existente, para que
     * las acciones mcp_call_tool muestren un icono significativo (teclado, ratón,
     * cámara…) en vez del engranaje genérico.
     */
    _resolveMcpIconType(params) {
        const tool = String((params && params.tool) || '').toLowerCase();
        const map = {
            press_key: 'press',
            press_key_combination: 'hotkey',
            hold_key: 'press',
            type_text: 'type',
            get_screenshot: 'screenshot',
            get_screen_size: 'screenshot',
            click_at: 'click',
            click_mouse: 'click',
            double_click: 'double_click',
            move_mouse: 'move',
            drag_mouse: 'drag',
            scroll_mouse: 'scroll',
            get_cursor_position: 'move',
            focus_window: 'open_application',
            get_active_window: 'browser_snapshot',
            set_clipboard_content: 'type',
            get_clipboard_content: 'file_read_text',
        };
        return map[tool] || null;
    }

    _getActionIcon(type, params) {
        // mcp_call_tool: usar el icono de la tool subyacente cuando se reconoce.
        if (type === 'mcp_call_tool') {
            const resolved = this._resolveMcpIconType(params);
            if (resolved) type = resolved;
        }
        const icons = {
            screenshot: 'camera',
            browser_screenshot: 'camera',
            adb_screenshot: 'camera',
            click: 'mouse-pointer-click',
            double_click: 'mouse-pointer-click',
            right_click: 'mouse-pointer-click',
            browser_click: 'mouse-pointer-click',
            type: 'keyboard',
            press: 'keyboard',
            hotkey: 'keyboard',
            browser_type: 'keyboard',
            open_application: 'app-window',
            browser_navigate: 'globe',
            browser_use_automation_profile: 'globe',
            browser_extract: 'file-text',
            browser_snapshot: 'clipboard-list',
            browser_scroll: 'arrow-up-down',
            terminal_run: 'terminal',
            scroll: 'arrow-up-down',
            screen_read_text: 'scan-text',
            move: 'move',
            drag: 'hand',
            wait: 'clock',
            file_write_text: 'file-pen',
            file_read_text: 'file-text',
            file_exists: 'file-check',
            task_complete: 'circle-check',
            generate_image: 'image',
            generate_video: 'video',
            generate_music: 'music',
            delegate_computer_use: 'monitor',
        };
        return window.gminiDom.icon(icons[type] || 'settings-2');
    }

    /**
     * Etiqueta legible para una llamada mcp_call_tool, según la tool y sus
     * argumentos (ej. "Tecla: enter", "Escribiendo: notepad", "Click en (720, 450)").
     */
    _getMcpLabel(params) {
        const tool = String((params && params.tool) || '').toLowerCase();
        const a = (params && params.arguments) || {};
        const clip = (t) => {
            const s = String(t == null ? '' : t);
            return s.length > 32 ? s.slice(0, 32) + '…' : s;
        };
        switch (tool) {
            case 'press_key': return `Tecla: ${a.key || '?'}`;
            case 'press_key_combination': return `Atajo: ${Array.isArray(a.keys) ? a.keys.join(' + ') : (a.keys || '?')}`;
            case 'hold_key': return `Mantener tecla: ${a.key || '?'}`;
            case 'type_text': return `Escribiendo: "${clip(a.text)}"`;
            case 'get_screenshot': return 'Captura de pantalla';
            case 'get_screen_size': return 'Tamaño de pantalla';
            case 'click_at': return `Click en (${a.x}, ${a.y})`;
            case 'click_mouse': return 'Click del ratón';
            case 'double_click': return (a.x != null) ? `Doble click en (${a.x}, ${a.y})` : 'Doble click';
            case 'move_mouse': return `Mover cursor a (${a.x}, ${a.y})`;
            case 'drag_mouse': return `Arrastrar (${a.fromX}, ${a.fromY}) → (${a.toX}, ${a.toY})`;
            case 'scroll_mouse': return `Scroll ${Number(a.amount) >= 0 ? 'abajo' : 'arriba'}`;
            case 'focus_window': return `Enfocar ventana: ${clip(a.title)}`;
            case 'get_active_window': return 'Ventana activa';
            case 'set_clipboard_content': return 'Copiar al portapapeles';
            case 'get_clipboard_content': return 'Leer portapapeles';
            default: return tool ? `MCP: ${tool}` : `MCP: ${params.server_id || 'tool'}`;
        }
    }

    _getActionLabel(type, params) {
        switch (type) {
            case 'screenshot': return 'Captura de pantalla';
            case 'screen_read_text': return 'Leyendo texto de pantalla (OCR)';
            case 'delegate_computer_use': return 'Delegando a computer use';
            case 'mcp_call_tool': return this._getMcpLabel(params);
            case 'click': return `Click en (${params.x}, ${params.y})`;
            case 'double_click': return `Doble click en (${params.x}, ${params.y})`;
            case 'right_click': return `Click derecho en (${params.x}, ${params.y})`;
            case 'type': return `Escribiendo texto`;
            case 'press': return `Tecla: ${params.key || '?'}`;
            case 'hotkey': return `Atajo: ${Array.isArray(params.keys) ? params.keys.join(' + ') : (params.keys || '?')}`;
            case 'open_application': return `Abriendo: ${params.name || '?'}`;
            case 'browser_navigate': return `Navegando a URL`;
            case 'browser_click': return `Click en elemento web`;
            case 'browser_type': return `Escribiendo en campo web`;
            case 'browser_extract': return `Extrayendo contenido web`;
            case 'browser_snapshot': return `Capturando DOM del navegador`;
            case 'browser_tabs': return `Listando pestañas`;
            case 'browser_new_tab': return `Abriendo nueva pestaña`;
            case 'browser_switch_tab': return `Cambiando de pestaña`;
            case 'browser_close_tab': return `Cerrando pestaña`;
            case 'browser_go_back': return `Volviendo atrás en navegador`;
            case 'browser_go_forward': return `Avanzando en navegador`;
            case 'browser_scroll': return `Scroll en navegador`;
            case 'terminal_run': return 'Ejecutando comando';
            case 'scroll': return `Scroll ${(params.clicks || 0) > 0 ? 'abajo' : 'arriba'} (${Math.abs(params.clicks || 0)} pasos)`;
            case 'move': return `Mover cursor a (${params.x}, ${params.y})`;
            case 'drag': return `Arrastrar a (${params.x}, ${params.y})`;
            case 'wait': return `Esperando ${params.seconds || 1}s`;
            case 'generate_image': return `Generando imagen con IA`;
            case 'generate_video': return `Generando video con IA`;
            case 'generate_music': return `Generando música con IA`;
            default: return type.replace(/_/g, ' ');
        }
    }

    _formatActionParams(type, params) {
        if (!params || Object.keys(params).length === 0) return '';
        // Todo lo que llega en params lo decide el modelo (o viene del historial):
        // se escapa siempre y las coordenadas/contadores solo se muestran si son números (SEC1).
        const esc = (v) => this._escapeHtml(v);
        const num = (v) => (v !== null && v !== '' && Number.isFinite(Number(v)) ? String(Number(v)) : '?');
        const code = (v) => `<code>${esc(v ?? '')}</code>`;
        const tag = (v) => ` <span class="action-param-tag">${esc(v)}</span>`;
        const detail = (v) => `<span class="action-param-detail">${esc(v)}</span>`;
        switch (type) {
            case 'type': return code(params.text) + (params.submit ? tag('+ Enter') : '');
            case 'delegate_computer_use': return code(params.task) + (params.monitor != null ? tag(`monitor ${num(params.monitor)}`) : '');
            case 'terminal_run': return code(params.command);
            case 'browser_navigate': return code(params.url);
            case 'browser_click': return `selector: ${code(params.selector)}` + (params.force ? tag('force') : '');
            case 'browser_type': return `selector: ${code(params.selector)} → ${code(params.text)}`;
            case 'generate_image': return code(params.prompt) + (params.aspect_ratio ? tag(params.aspect_ratio) : '');
            case 'generate_video': return code(params.prompt) + (params.duration_seconds ? tag(`${num(params.duration_seconds)}s`) : '');
            case 'generate_music': return code(params.prompt);
            case 'click': {
                const clicks = Number(params.clicks) || 1;
                return detail(`botón: ${params.button || 'left'}${clicks > 1 ? `, ${num(clicks)} clics` : ''}`);
            }
            case 'double_click': return detail(`botón: ${params.button || 'left'}`);
            case 'right_click': return detail(`en (${num(params.x)}, ${num(params.y)})`);
            case 'screenshot': return params.monitor != null ? detail(`monitor: ${num(params.monitor)}`) : '';
            case 'open_application': return params.name ? detail(params.name) : '';
            case 'hotkey': return code(Array.isArray(params.keys) ? params.keys.join(' + ') : params.keys);
            case 'press': return code(params.key);
            case 'scroll': return detail(`${Math.abs(Number(params.clicks) || 0)} clics${params.x != null ? ` en (${num(params.x)}, ${num(params.y)})` : ''}`);
            case 'drag': return detail(`de (${num(params.startX)}, ${num(params.startY)}) a (${num(params.x)}, ${num(params.y)})`);
            case 'browser_switch_tab': return params.tab_id != null ? detail(`pestaña: ${params.tab_id}`) : '';
            default: {
                // Tools sin formato propio: todos los params como texto compacto.
                const summary = Object.entries(params)
                    .filter(([, v]) => v !== undefined && v !== null && v !== '')
                    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
                    .join(' | ');
                return summary ? detail(summary) : '';
            }
        }
    }

    renderApprovalState(data) {
        if (!data?.pending) {
            if (this.approvalCardEl) {
                this.approvalCardEl.remove();
                this.approvalCardEl = null;
            }
            return;
        }

        if (!this.approvalCardEl) {
            this.approvalCardEl = document.createElement('div');
            this.approvalCardEl.className = 'message approval-message';
            this.messagesContainer.appendChild(this.approvalCardEl);
        }

        const findings = Array.isArray(data.findings) ? data.findings : [];
        const approvalKind = data.kind || 'approval';
        const isDryRun = approvalKind === 'dry_run';
        const findingsHtml = findings.map((item) => {
            const capability = item.capability_label ? `<div class="approval-meta">Permiso: ${this._escapeHtml(item.capability_label)}</div>` : '';
            const confidence = typeof item.confidence === 'number' && typeof item.threshold === 'number'
                ? `<div class="approval-meta">Score ${item.confidence.toFixed(2)} / ${item.threshold.toFixed(2)}</div>`
                : '';
            const spendMode = item.spend_policy_mode
                ? `<div class="approval-meta">Política de gasto: ${this._escapeHtml(item.spend_policy_mode)}</div>`
                : '';
            const spendAmount = typeof item.amount_usd === 'number'
                ? `<div class="approval-meta">Monto detectado: $${this._escapeHtml(item.amount_usd.toFixed(2))} USD</div>`
                : (typeof item.raw_amount === 'number' && item.payment_currency
                    ? `<div class="approval-meta">Monto detectado: ${this._escapeHtml(item.raw_amount.toFixed(2))} ${this._escapeHtml(item.payment_currency)}</div>`
                    : '');
            const paymentAccount = item.payment_account_name
                ? `<div class="approval-meta">Cuenta: ${this._escapeHtml(item.payment_account_name)}${item.payment_account_last4 ? ` • ****${this._escapeHtml(item.payment_account_last4)}` : ''}</div>`
                : (item.payment_account_requested
                    ? `<div class="approval-meta">Cuenta solicitada: ${this._escapeHtml(item.payment_account_requested)}</div>`
                    : '');
            return `
                <div class="approval-finding">
                    <div><strong>${this._escapeHtml(item.action || 'accion')}</strong> <span class="approval-severity">${this._escapeHtml(item.severity || '')}</span></div>
                    <div>${this._escapeHtml(item.reason || '')}</div>
                    ${capability}
                    ${confidence}
                    ${spendMode}
                    ${spendAmount}
                    ${paymentAccount}
                </div>
            `;
        }).join('');

        const title = isDryRun ? 'Dry Run requerido' : 'Aprobación requerida';
        const approveLabel = isDryRun ? 'Ejecutar' : 'Aprobar';
        const decisionBadge = data.decision
            ? `<div class="approval-meta">Critic: ${this._escapeHtml(data.decision)}</div>`
            : '';

        this.approvalCardEl.innerHTML = `
            <div class="approval-header">
                <div class="approval-title">${title}</div>
                <div class="approval-badge">${this._escapeHtml(data.mode_name || data.mode || 'modo activo')}</div>
            </div>
            ${decisionBadge}
            <div class="approval-summary">${this._renderMarkdown(data.summary || '')}</div>
            <div class="approval-findings">${findingsHtml}</div>
            <div class="approval-actions">
                <button class="approval-btn approval-approve" type="button" data-action="approve">${approveLabel}</button>
                <button class="approval-btn approval-cancel" type="button" data-action="cancel">Cancelar</button>
            </div>
        `;

        const card = this.approvalCardEl;
        const decide = (command) => {
            card.querySelectorAll('.approval-btn').forEach((b) => { b.disabled = true; });
            ws.sendCommand(command);
        };
        card.querySelector('[data-action="approve"]')?.addEventListener('click', () => decide('approve_pending'));
        card.querySelector('[data-action="cancel"]')?.addEventListener('click', () => decide('cancel_pending'));
        this.hideTyping();

        this._scrollToBottom();
    }
}

const chatManager = new ChatManager();
