// Mini-chat burbuja del avatar (Fase 5).
// Proxy IPC hacia mainWindow: NO abre un segundo socket, reusa la sesion
// del chat principal via skin:chat-send / skin-chat-relay.

const gmini = window.gmini;

const root = document.getElementById('skin-chat');
const messagesEl = document.getElementById('skin-chat-messages');
const form = document.getElementById('skin-chat-form');
const input = document.getElementById('skin-chat-input');

const MAX_MESSAGES = 6;
let isOpen = false;
let streamingBubble = null;

function isChatOpen() {
    return isOpen;
}

function addMessage(role, text) {
    const el = document.createElement('div');
    el.className = `skin-chat-msg skin-chat-msg-${role}`;
    el.textContent = text;
    messagesEl.appendChild(el);
    while (messagesEl.children.length > MAX_MESSAGES) {
        messagesEl.removeChild(messagesEl.firstChild);
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
}

async function open() {
    if (isOpen) return;
    isOpen = true;
    root.classList.add('visible');
    if (gmini && typeof gmini.skinChatOpen === 'function') {
        await gmini.skinChatOpen().catch(() => {});
    }
    if (gmini && typeof gmini.skinSetInteractive === 'function') {
        await gmini.skinSetInteractive(true).catch(() => {});
    }
    input.focus();
}

async function close() {
    if (!isOpen) return;
    isOpen = false;
    root.classList.remove('visible');
    streamingBubble = null;
    if (gmini && typeof gmini.skinChatClose === 'function') {
        await gmini.skinChatClose().catch(() => {});
    }
    if (gmini && typeof gmini.skinSetInteractive === 'function') {
        await gmini.skinSetInteractive(false).catch(() => {});
    }
}

function toggle() {
    if (isOpen) {
        void close();
    } else {
        void open();
    }
}

form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    addMessage('user', text);
    streamingBubble = null;
    input.value = '';
    if (gmini && typeof gmini.skinChatSend === 'function') {
        void gmini.skinChatSend(text);
    }
});

// Evita que clicks/drag dentro de la burbuja lleguen al canvas (drag/wheel).
['mousedown', 'mouseup', 'wheel', 'click'].forEach((evt) => {
    root.addEventListener(evt, (event) => event.stopPropagation());
});

if (gmini && typeof gmini.onSkinChatRelay === 'function') {
    gmini.onSkinChatRelay(({ event, data } = {}) => {
        if (event !== 'agent:message') return;
        const text = String(data?.text ?? '');
        const type = data?.type || 'text';

        if (type !== 'text') {
            if (text) addMessage('system', text);
            return;
        }

        if (data?.done) {
            streamingBubble = null;
            return;
        }
        if (!text) return;
        if (!streamingBubble) {
            streamingBubble = addMessage('agent', '');
        }
        streamingBubble.textContent += text;
        messagesEl.scrollTop = messagesEl.scrollHeight;
    });
}

export const skinChat = { isChatOpen, toggle, open, close };
