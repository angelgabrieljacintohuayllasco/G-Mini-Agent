/**
 * G-Mini Agent — Chat Module
 * Renderizado de mensajes, streaming y Markdown básico.
 */

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
        // Nada extra por ahora
        this._lastScreenshotTime = 0;
    }

    /**
     * Añade un mensaje del usuario al chat.
     */
    addUserMessage(text) {
        const el = this._createMessageEl('user-message');
        el.innerHTML = this._escapeHtml(text);
        this.messagesContainer.appendChild(el);
        this._scrollToBottom();
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
            errDiv.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/><circle cx="12" cy="13" r="4"/></svg> Captura no disponible';
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
        const wrap = (inner) => `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
        switch (name) {
            case 'download': return wrap('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>');
            case 'zoom': return wrap('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>');
            case 'zoom-in': return wrap('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>');
            case 'zoom-out': return wrap('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/>');
            case 'fullscreen': return wrap('<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>');
            case 'reset': return wrap('<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>');
            case 'close': return wrap('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>');
            default: return wrap('');
        }
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
        const existing = document.getElementById('screenshot-modal');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'screenshot-modal';
        overlay.className = 'screenshot-modal-overlay';

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
        bar.appendChild(mk('close', 'Cerrar', () => overlay.remove()));

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
        overlay.addEventListener('click', (e) => { if (e.target === overlay || e.target === stage) overlay.remove(); });
        const onKey = (e) => {
            if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', onKey); }
            else if (e.key === '+' || e.key === '=') setScale(scale * 1.25);
            else if (e.key === '-') setScale(scale / 1.25);
            else if (e.key === '0') setScale(1);
        };
        document.addEventListener('keydown', onKey);

        overlay.appendChild(bar);
        overlay.appendChild(stage);
        document.body.appendChild(overlay);
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
        this.isStreaming = true;
        this.streamingText = '';
        this.currentStreamingEl = this._createMessageEl('assistant-message streaming-cursor');
        this.messagesContainer.appendChild(this.currentStreamingEl);
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
        if (!this.currentStreamingEl) return;
        const cleanText = this._stripActionLines(this.streamingText);
        if (!cleanText) {
            this.currentStreamingEl.style.display = 'none';
        } else {
            this.currentStreamingEl.style.display = '';
            this.currentStreamingEl.innerHTML = this._renderMarkdown(cleanText);
        }
        this._scrollToBottom();
    }

    _addErrorMessage(text) {
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

    _scrollToBottom() {
        const container = document.getElementById('chat-container');
        if (!container) return;
        requestAnimationFrame(() => {
            container.scrollTop = container.scrollHeight;
        });
    }

    /**
     * Renderizado básico de Markdown.
     */
    _renderMarkdown(text) {
        if (!text) return '';

        let html = this._escapeHtml(text);

        // Code blocks ```...```
        html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
            return `<pre><code class="lang-${lang}">${code.trim()}</code></pre>`;
        });

        // Inline code `...`
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold **...**
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Italic *...*
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Line breaks
        html = html.replace(/\n/g, '<br>');

        return html;
    }

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    clear() {
        this.messagesContainer.innerHTML = '';
        this.finishStreaming();
        this.approvalCardEl = null;
    }

    // ── Action activity cards ────────────────────────

    /**
     * Muestra una tarjeta de actividad en el chat indicando qué herramienta se ejecutó.
     * @param {string} type - Nombre de la herramienta (click, type, screenshot, etc.)
     * @param {object} params - Parámetros de la herramienta
     * @returns {HTMLElement} El elemento de la card para poder actualizarlo con el resultado
     */
    addActionCard(type, params) {
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
                if (bar) bar.style.opacity = '0';
            }, 600);
        }

        const statusEl = cardEl.querySelector('.action-status');
        if (statusEl) {
            const checkSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
            const xSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
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
        const s = (d) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
        // mcp_call_tool: usar el icono de la tool subyacente cuando se reconoce.
        if (type === 'mcp_call_tool') {
            const resolved = this._resolveMcpIconType(params);
            if (resolved) type = resolved;
        }
        const icons = {
            screenshot: s('<path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/><circle cx="12" cy="13" r="4"/>'),
            click: s('<path d="M4 4l7.07 17 2.51-7.39L21 11.07z"/>'),
            double_click: s('<path d="M4 4l7.07 17 2.51-7.39L21 11.07z"/>'),
            right_click: s('<path d="M4 4l7.07 17 2.51-7.39L21 11.07z"/>'),
            type: s('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M7 16h10"/>'),
            press: s('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M7 16h10"/>'),
            hotkey: s('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M7 16h10"/>'),
            open_application: s('<polygon points="5 3 19 12 5 21 5 3"/>'),
            browser_navigate: s('<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/>'),
            browser_click: s('<path d="M4 4l7.07 17 2.51-7.39L21 11.07z"/>'),
            browser_type: s('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M7 16h10"/>'),
            browser_extract: s('<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'),
            browser_snapshot: s('<path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/>'),
            browser_use_automation_profile: s('<circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10"/>'),
            terminal_run: s('<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>'),
            scroll: s('<path d="M12 5v14M5 12l7-7 7 7"/>'),
            screen_read_text: s('<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'),
            move: s('<path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20"/>'),
            drag: s('<path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20"/>'),
            wait: s('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>'),
            file_write_text: s('<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>'),
            file_read_text: s('<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'),
            file_exists: s('<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'),
            task_complete: s('<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'),
            generate_image: s('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>'),
            generate_video: s('<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/>'),
            generate_music: s('<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'),
        };
        return icons[type] || s('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>');
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
            case 'hotkey': return `Hotkey: ${params.keys || '?'}`;
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
        switch (type) {
            case 'type': return `<code>${this._escapeHtml(params.text || '')}</code>${params.submit ? ' <span class="action-param-tag">+ Enter</span>' : ''}`;
            case 'delegate_computer_use': return `<code>${this._escapeHtml(params.task || '')}</code>${params.monitor ? ` <span class="action-param-tag">monitor ${params.monitor}</span>` : ''}`;
            case 'terminal_run': return `<code>${this._escapeHtml(params.command || '')}</code>`;
            case 'browser_navigate': return `<code>${this._escapeHtml(params.url || '')}</code>`;
            case 'browser_click': return `selector: <code>${this._escapeHtml(params.selector || '')}</code>${params.force ? ' <span class="action-param-tag">force</span>' : ''}`;
            case 'browser_type': return `selector: <code>${this._escapeHtml(params.selector || '')}</code> → <code>${this._escapeHtml(params.text || '')}</code>`;
            case 'generate_image': return `<code>${this._escapeHtml(params.prompt || '')}</code>${params.aspect_ratio ? ` <span class="action-param-tag">${params.aspect_ratio}</span>` : ''}`;
            case 'generate_video': return `<code>${this._escapeHtml(params.prompt || '')}</code>${params.duration_seconds ? ` <span class="action-param-tag">${params.duration_seconds}s</span>` : ''}`;
            case 'generate_music': return `<code>${this._escapeHtml(params.prompt || '')}</code>`;
            case 'click': return `<span class="action-param-detail">botón: ${params.button || 'left'}${(params.clicks || 1) > 1 ? ` × ${params.clicks}` : ''}</span>`;
            case 'double_click': return `<span class="action-param-detail">botón: ${params.button || 'left'}</span>`;
            case 'right_click': return `<span class="action-param-detail">en (${params.x}, ${params.y})</span>`;
            case 'screenshot': return params.monitor != null ? `<span class="action-param-detail">monitor: ${params.monitor}</span>` : '';
            case 'open_application': return params.name ? `<span class="action-param-detail">${this._escapeHtml(params.name)}</span>` : '';
            case 'hotkey': return `<code>${this._escapeHtml(params.keys || '')}</code>`;
            case 'press': return `<code>${this._escapeHtml(params.key || '')}</code>`;
            case 'scroll': return `<span class="action-param-detail">${Math.abs(params.clicks || 0)} clicks${params.x != null ? ` en (${params.x}, ${params.y})` : ''}</span>`;
            case 'drag': return `<span class="action-param-detail">de (${params.startX || '?'}, ${params.startY || '?'}) a (${params.x}, ${params.y})</span>`;
            case 'browser_switch_tab': return params.tab_id ? `<span class="action-param-detail">tab: ${params.tab_id}</span>` : '';
            default: {
                // Mostrar todos los params como JSON compacto para tools no mapeadas
                const summary = Object.entries(params)
                    .filter(([, v]) => v !== undefined && v !== null && v !== '')
                    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
                    .join(' | ');
                return summary ? `<span class="action-param-detail">${this._escapeHtml(summary)}</span>` : '';
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

        const title = isDryRun ? 'Dry Run requerido' : 'Aprobacion requerida';
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
                <button class="approval-btn approval-approve" data-action="approve">${approveLabel}</button>
                <button class="approval-btn approval-cancel" data-action="cancel">Cancelar</button>
            </div>
        `;

        this.approvalCardEl.querySelector('[data-action="approve"]')?.addEventListener('click', () => {
            ws.sendCommand('approve_pending');
        });
        this.approvalCardEl.querySelector('[data-action="cancel"]')?.addEventListener('click', () => {
            ws.sendCommand('cancel_pending');
        });

        this._scrollToBottom();
    }
}

const chatManager = new ChatManager();
