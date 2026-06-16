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
        this.currentSessionId = null;
        this.sessions = [];
        this.isSidebarCollapsed = false;
    }

    init() {
        this._bindEvents();
        this.loadSessions();
    }

    _bindEvents() {
        // Toggle sidebar
        this.btnToggle?.addEventListener('click', () => this.toggleSidebar());

        // New chat button
        this.btnNewChat?.addEventListener('click', () => this.createNewChat());

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

    toggleSidebar() {
        this.isSidebarCollapsed = !this.isSidebarCollapsed;
        this.sidebar?.classList.toggle('collapsed', this.isSidebarCollapsed);
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

    _renderChatList() {
        if (!this.chatList) return;

        if (this.sessions.length === 0) {
            this.chatList.innerHTML = `
                <div class="chat-list-empty">
                    <div class="chat-list-empty-icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg></div>
                    <div>No hay conversaciones guardadas</div>
                </div>
            `;
            return;
        }

        const html = this.sessions.map(session => {
            const isActive = session.session_id === this.currentSessionId;
            const title = session.title || this._generateTitle(session);
            const date = this._formatDate(session.updated_at);
            const count = session.message_count || 0;
            const mode = session.mode || 'normal';
            const escapedId = this._escapeHtml(session.session_id);

            return `
                <div class="chat-item ${isActive ? 'active' : ''}" 
                     data-session-id="${escapedId}">
                    <div class="chat-item-title">${this._escapeHtml(title)}</div>
                    <div class="chat-item-meta">
                        <span class="chat-item-date">${date}</span>
                        <span class="chat-item-date">${this._escapeHtml(mode)}</span>
                        <span class="chat-item-count">${count} msgs</span>
                        <span class="chat-item-actions">
                            <button class="btn-delete-chat"
                                    title="Eliminar"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>
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
            
            // Clear chat UI
            chatManager.clear();
            chatManager.messagesContainer.innerHTML = `
                <div class="message system-message">
                    <p>Nueva conversación. Escribe un mensaje para comenzar.</p>
                </div>
            `;
            
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

            const response = await fetch(`http://127.0.0.1:8765/api/sessions/${sessionId}/load`, {
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
            } else {
                chatManager.messagesContainer.innerHTML = `
                    <div class="message system-message">
                        <p>Conversación cargada. Sin mensajes.</p>
                    </div>
                `;
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
            const response = await fetch(`http://127.0.0.1:8765/api/sessions/${sessionId}`, {
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

    // Called after a message is sent/received to update the session
    async refreshCurrentSession() {
        await this.loadSessions();
    }

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

const historyManager = new HistoryManager();
