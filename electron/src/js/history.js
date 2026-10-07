/**
 * G-Mini Agent — History Module
 * Gestión del historial de conversaciones (sidebar).
 */

class HistoryManager {
    constructor() {
        this.sidebar = document.getElementById('sidebar');
        this.chatList = document.getElementById('chat-list');
        this.btnToggle = document.getElementById('btn-toggle-sidebar');
        this.btnNewChat = document.getElementById('btn-new-chat');
        this.searchInput = document.getElementById('chat-search');
        this.searchQuery = '';
        this.currentSessionId = null;
        this.sessions = [];
        this.isSidebarCollapsed = false;
        // Por debajo de este ancho el historial es un cajón sobre el chat (B15).
        this._narrowQuery = window.matchMedia('(max-width: 759px)');
    }

    init() {
        this._restoreSidebarState();
        this._bindEvents();
        this.loadSessions();
    }

    _restoreSidebarState() {
        const saved = () => {
            try { return localStorage.getItem('gmini_sidebar_collapsed') === 'true'; } catch (e) { return false; }
        };
        // En ventana estrecha arranca cerrado; en escritorio se recuerda la última elección.
        this.isSidebarCollapsed = this._narrowQuery.matches ? true : saved();
        this._applySidebarState(false);
        this._narrowQuery.addEventListener('change', (e) => {
            this.isSidebarCollapsed = e.matches ? true : saved();
            this._applySidebarState(false);
        });
    }

    _applySidebarState(persist) {
        this.sidebar?.classList.toggle('collapsed', this.isSidebarCollapsed);
        this.btnToggle?.setAttribute('aria-expanded', this.isSidebarCollapsed ? 'false' : 'true');
        if (persist && !this._narrowQuery.matches) {
            try { localStorage.setItem('gmini_sidebar_collapsed', String(this.isSidebarCollapsed)); } catch (e) { /* sin storage */ }
        }
        window.gminiLayout?.syncScrim();
    }

    isNarrow() {
        return this._narrowQuery.matches;
    }

    _bindEvents() {
        // Toggle sidebar
        this.btnToggle?.addEventListener('click', () => this.toggleSidebar());

        // New chat button
        this.btnNewChat?.addEventListener('click', () => this.createNewChat());
        document.getElementById('btn-export-chat')?.addEventListener('click', () => this.exportCurrentConversation());

        // Búsqueda: filtra en el cliente las conversaciones ya cargadas.
        this.searchInput?.addEventListener('input', () => {
            this.searchQuery = this.searchInput.value.trim();
            this._renderChatList();
        });
        this.searchInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.searchInput.value) {
                e.preventDefault();
                e.stopPropagation();
                this.searchInput.value = '';
                this.searchQuery = '';
                this._renderChatList();
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                this.chatList?.querySelector('.chat-item')?.focus();
            }
        });

        // Lista accesible con teclado: Enter/Espacio cargan, flechas navegan, Supr elimina.
        this.chatList?.addEventListener('keydown', (e) => {
            const item = e.target.closest('.chat-item');
            if (!item || e.target !== item) return;
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                this.loadSession(item.dataset.sessionId);
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const sibling = e.key === 'ArrowDown' ? item.nextElementSibling : item.previousElementSibling;
                if (sibling?.classList.contains('chat-item')) sibling.focus();
                else if (e.key === 'ArrowUp') this.searchInput?.focus();
            } else if (e.key === 'Delete') {
                e.preventDefault();
                this.deleteSession(item.dataset.sessionId);
            }
        });

        // Keyboard shortcut: Ctrl+N for new chat
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'n') {
                e.preventDefault();
                this.createNewChat();
            }
            // Ctrl+H to toggle sidebar
            if (e.ctrlKey && e.key === 'h') {
                e.preventDefault();
                this.toggleSidebar();
            }
        });
    }

    /** Sin argumento alterna; con booleano fuerza abierto (true) o cerrado (false). */
    toggleSidebar(forceOpen) {
        this.isSidebarCollapsed = typeof forceOpen === 'boolean' ? !forceOpen : !this.isSidebarCollapsed;
        this._applySidebarState(true);
    }

    async loadSessions() {
        try {
            const response = await fetch('http://127.0.0.1:8765/api/sessions');
            if (!response.ok) throw new Error('Failed to load sessions');
            
            const data = await response.json();
            this.sessions = data.sessions || [];
            this.currentSessionId = data.current_session;
            this._renderChatList();
        } catch (error) {
            console.error('Error loading sessions:', error);
            this._renderChatList();
        }
    }

    /** Coincidencia sin acentos ni mayúsculas sobre título, modo y fecha. */
    _matchesSearch(session) {
        if (!this.searchQuery) return true;
        const norm = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
        const haystack = norm([
            session.title || this._generateTitle(session),
            session.mode,
            this._formatDate(session.updated_at),
        ].join(' '));
        return norm(this.searchQuery).split(/\s+/).every((word) => haystack.includes(word));
    }

    focusSearch() {
        if (this.isSidebarCollapsed) this.toggleSidebar(true);
        requestAnimationFrame(() => {
            this.searchInput?.focus();
            this.searchInput?.select();
        });
    }

    _renderChatList() {
        if (!this.chatList) return;

        const visible = this.sessions.filter((session) => this._matchesSearch(session));
        if (this.sessions.length > 0 && visible.length === 0) {
            this.chatList.innerHTML = `
                <div class="chat-list-empty">
                    <div class="chat-list-empty-icon">${window.gminiDom.icon('search', 'icon-xl')}</div>
                    <div>Sin resultados para "${this._escapeHtml(this.searchQuery)}"</div>
                </div>
            `;
            return;
        }

        if (this.sessions.length === 0) {
            this.chatList.innerHTML = `
                <div class="chat-list-empty">
                    <div class="chat-list-empty-icon">${window.gminiDom.icon('message-square', 'icon-xl')}</div>
                    <div>No hay conversaciones guardadas</div>
                </div>
            `;
            return;
        }

        const html = visible.map(session => {
            const isActive = session.session_id === this.currentSessionId;
            const title = session.title || this._generateTitle(session);
            const date = this._formatDate(session.updated_at);
            const count = session.message_count || 0;
            const mode = session.mode || 'normal';
            const escapedId = this._escapeHtml(session.session_id);

            return `
                <div class="chat-item ${isActive ? 'active' : ''}" role="button" tabindex="0"
                     ${isActive ? 'aria-current="true"' : ''} data-session-id="${escapedId}">
                    <div class="chat-item-title">${this._escapeHtml(title)}</div>
                    <div class="chat-item-meta">
                        <span class="chat-item-date">${date}</span>
                        <span class="chat-item-date">${this._escapeHtml(mode)}</span>
                        <span class="chat-item-count">${count} msgs</span>
                        <span class="chat-item-actions">
                            <button class="btn-delete-chat"
                                    title="Eliminar conversación" aria-label="Eliminar conversación">${window.gminiDom.icon('trash-2', 'icon-sm')}</button>
                        </span>
                    </div>
                </div>
            `;
        }).join('');

        this.chatList.innerHTML = html;

        // Event delegation — evita inyectar session_id en onclick inline
        this.chatList.querySelectorAll('.chat-item').forEach(el => {
            el.addEventListener('click', () => this.loadSession(el.dataset.sessionId));
        });
        this.chatList.querySelectorAll('.btn-delete-chat').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deleteSession(btn.closest('.chat-item').dataset.sessionId);
            });
        });
    }

    _generateTitle(session) {
        // Generate title from session_id or first message
        const id = session.session_id || '';
        const match = id.match(/ses_(\d{8})_(\d{6})/);
        if (match) {
            const date = match[1];
            const time = match[2];
            return `Chat ${date.slice(6,8)}/${date.slice(4,6)} ${time.slice(0,2)}:${time.slice(2,4)}`;
        }
        return 'Conversación';
    }

    _formatDate(isoString) {
        if (!isoString) return '';
        try {
            const date = new Date(isoString);
            const now = new Date();
            const diff = now - date;
            
            // Today
            if (diff < 24 * 60 * 60 * 1000 && date.getDate() === now.getDate()) {
                return date.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' });
            }
            // Yesterday
            if (diff < 48 * 60 * 60 * 1000) {
                return 'Ayer';
            }
            // This week
            if (diff < 7 * 24 * 60 * 60 * 1000) {
                return date.toLocaleDateString('es', { weekday: 'short' });
            }
            // Older
            return date.toLocaleDateString('es', { day: '2-digit', month: '2-digit' });
        } catch {
            return '';
        }
    }

    async createNewChat() {
        try {
            // Solo llamamos al backend para crear nueva sesión en memoria
            // La sesión se guardará en DB solo cuando se envíe el primer mensaje
            const response = await fetch('http://127.0.0.1:8765/api/sessions/new', {
                method: 'POST',
            });
            if (!response.ok) throw new Error('Failed to create session');
            
            const data = await response.json();
            this.currentSessionId = data.session_id;
            if (window.settingsManager?.refreshModesFromBackend) {
                await window.settingsManager.refreshModesFromBackend();
            }
            
            // Clear chat UI (vuelve el estado vacío con sugerencias)
            chatManager.clear();
            window.gminiComposer?.focus();
            
            // NO recargamos las sesiones aquí - la nueva sesión no existe en DB aún
            // Se actualizará cuando el usuario envíe el primer mensaje
            this._updateActiveState();
        } catch (error) {
            console.error('Error creating new chat:', error);
        }
    }

    _updateActiveState() {
        // Quitar estado activo de todos los items
        document.querySelectorAll('.chat-item').forEach(el => {
            el.classList.remove('active');
        });
    }

    async loadSession(sessionId) {
        if (sessionId === this.currentSessionId) return;

        try {
            // Detener generación activa antes de cambiar de sesión
            ws.sendCommand('stop');

            const response = await fetch(`http://127.0.0.1:8765/api/sessions/${encodeURIComponent(sessionId)}/load`, {
                method: 'POST',
            });
            if (!response.ok) throw new Error('Failed to load session');
            
            const data = await response.json();
            this.currentSessionId = sessionId;
            if (window.settingsManager?.refreshModesFromBackend) {
                await window.settingsManager.refreshModesFromBackend();
            }
            
            // Clear and repopulate chat
            chatManager.clear();
            
            if (data.messages && data.messages.length > 0) {
                data.messages.forEach(msg => {
                    const meta = msg.metadata || {};
                    const msgType = msg.message_type || 'text';

                    // Tool calls se muestran como action cards completadas
                    if (meta.tool_name) {
                        const cardEl = chatManager.addActionCard(
                            meta.tool_name,
                            meta.params || {}
                        );
                        chatManager.updateActionCard(
                            cardEl,
                            meta.success !== false,
                            meta.result_preview || '',
                            meta.duration_ms
                        );
                    } else if (msg.role === 'display' || msgType === 'system' || msgType === 'action' || msgType === 'error' || msgType === 'warning') {
                        const cssClass = msgType === 'error' ? 'error-message'
                            : msgType === 'warning' ? 'warning-message'
                            : msgType === 'action' ? 'system-message action-result'
                            : 'system-message';
                        const el = chatManager._createMessageEl(cssClass);
                        el.innerHTML = chatManager._renderMarkdown(msg.content);
                        chatManager.messagesContainer.appendChild(el);
                    } else if (msg.role === 'user') {
                        chatManager.addUserMessage(msg.content);
                    } else if (msg.role === 'assistant') {
                        const el = chatManager._createMessageEl('assistant-message');
                        el.innerHTML = chatManager._renderMarkdown(msg.content);
                        chatManager.messagesContainer.appendChild(el);
                    }
                });
                chatManager._scrollToBottom();
            }
            
            // Update active state in sidebar
            this._renderChatList();
        } catch (error) {
            console.error('Error loading session:', error);
        }
    }

    async deleteSession(sessionId) {
        if (!confirm('¿Eliminar esta conversación?')) return;

        try {
            const response = await fetch(`http://127.0.0.1:8765/api/sessions/${encodeURIComponent(sessionId)}`, {
                method: 'DELETE',
            });
            if (!response.ok) throw new Error('Failed to delete session');
            
            // If deleted current session, create new one
            if (sessionId === this.currentSessionId) {
                await this.createNewChat();
            } else {
                await this.loadSessions();
            }
        } catch (error) {
            console.error('Error deleting session:', error);
        }
    }

    // ── Exportar la conversación actual a Markdown ─────────────

    async exportCurrentConversation() {
        const agentName = window.gminiIdentity?.name?.() || 'G-Mini';
        const session = this.sessions.find((s) => s.session_id === this.currentSessionId);
        const title = session ? (session.title || this._generateTitle(session)) : 'Conversación';
        let messages = null;
        if (this.currentSessionId) {
            try {
                const resp = await fetch(`http://127.0.0.1:8765/api/sessions/${encodeURIComponent(this.currentSessionId)}`);
                if (resp.ok) messages = (await resp.json()).messages || null;
            } catch (e) { /* se exporta lo que hay en pantalla */ }
        }
        const markdown = messages && messages.length
            ? this._markdownFromMessages(title, messages, agentName)
            : this._markdownFromScreen(title, agentName);
        if (!markdown) {
            chatManager._toast('Todavía no hay nada que exportar.');
            return;
        }
        const suggestedName = `${this._slug(title)}.md`;
        if (!window.gmini?.saveTextAs) {
            const link = document.createElement('a');
            link.href = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }));
            link.download = suggestedName;
            link.click();
            setTimeout(() => URL.revokeObjectURL(link.href), 1000);
            return;
        }
        const res = await window.gmini.saveTextAs({ suggestedName, content: markdown, kind: 'markdown' });
        if (res?.ok) chatManager._toast(`Conversación exportada: ${res.path}`);
        else if (!res?.canceled) chatManager._toast(`No se pudo exportar${res?.error ? `: ${res.error}` : ''}`, true);
    }

    _exportHeader(title) {
        return [`# ${title}`, '', `_Exportada desde G-Mini Agent el ${new Date().toLocaleString('es')}_`, ''];
    }

    _markdownFromMessages(title, messages, agentName) {
        const lines = this._exportHeader(title);
        for (const msg of messages) {
            const meta = msg.metadata || {};
            const stamp = msg.timestamp ? ` · ${this._formatStamp(msg.timestamp)}` : '';
            const content = String(msg.content || '').trim();
            if (meta.tool_name) {
                const outcome = meta.success !== false ? 'ok' : 'falló';
                const preview = meta.result_preview ? `: ${String(meta.result_preview).replace(/\s+/g, ' ').slice(0, 200)}` : '';
                lines.push(`> Acción \`${meta.tool_name}\` (${outcome})${preview}`, '');
            } else if (msg.role === 'user') {
                lines.push(`### Tú${stamp}`, '', content, '');
            } else if (msg.role === 'assistant') {
                lines.push(`### ${agentName}${stamp}`, '', content, '');
            } else if (content) {
                lines.push(`> ${content.replace(/\n/g, '\n> ')}`, '');
            }
        }
        return `${lines.join('\n').trim()}\n`;
    }

    /** Respaldo para conversaciones aún no guardadas: lo que se ve en el chat. */
    _markdownFromScreen(title, agentName) {
        const nodes = chatManager.messagesContainer.querySelectorAll('.message');
        if (!nodes.length) return '';
        const lines = this._exportHeader(title);
        nodes.forEach((node) => {
            const text = node.innerText.trim();
            if (!text) return;
            if (node.classList.contains('user-message')) lines.push('### Tú', '', text, '');
            else if (node.classList.contains('assistant-message')) lines.push(`### ${agentName}`, '', text, '');
            else lines.push(`> ${text.replace(/\n/g, '\n> ')}`, '');
        });
        return `${lines.join('\n').trim()}\n`;
    }

    _formatStamp(iso) {
        try {
            return new Date(iso).toLocaleString('es', { dateStyle: 'short', timeStyle: 'short' });
        } catch (e) {
            return '';
        }
    }

    _slug(text) {
        return String(text || 'conversacion')
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
            .slice(0, 60) || 'conversacion';
    }

    // Called after a message is sent/received to update the session
    async refreshCurrentSession() {
        await this.loadSessions();
    }

    _escapeHtml(text) {
        // Escapa también comillas: es seguro dentro de atributos (SEC3).
        return window.gminiDom.escapeHtml(text);
    }
}

const historyManager = new HistoryManager();
