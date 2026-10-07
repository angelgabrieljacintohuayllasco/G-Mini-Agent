/**
 * G-Mini Agent — Overlay Effects
 * Efectos visuales: indicadores de click, overlay de screenshot, modo transparente.
 */

class OverlayEffects {
    constructor() {
        this.clickIndicator = null;
        this.screenshotOverlay = null;
        this.isExecutingTask = false;
        this._init();
    }

    _init() {
        this._createClickIndicator();
        this._createScreenshotOverlay();
        this._createFullscreenOverlay();
    }

    /**
     * Crea el indicador circular de click
     */
    _createClickIndicator() {
        this.clickIndicator = document.createElement('div');
        this.clickIndicator.id = 'click-indicator';
        this.clickIndicator.innerHTML = `
            <div class="click-ring click-ring-outer"></div>
            <div class="click-ring click-ring-inner"></div>
            <div class="click-crosshair"></div>
        `;
        document.body.appendChild(this.clickIndicator);
    }

    /**
     * Crea el overlay de captura de pantalla
     */
    _createScreenshotOverlay() {
        this.screenshotOverlay = document.createElement('div');
        this.screenshotOverlay.id = 'screenshot-overlay';
        this.screenshotOverlay.innerHTML = `
            <div class="screenshot-flash"></div>
            <div class="screenshot-text">${window.gminiDom.icon('camera')} Capturando pantalla...</div>
        `;
        document.body.appendChild(this.screenshotOverlay);
    }

    /**
     * Crea el overlay fullscreen para visualizar acciones (se abre en ventana separada)
     */
    _createFullscreenOverlay() {
        // Este overlay se comunica con la ventana principal para mostrar clicks
        // en la pantalla completa del usuario
    }

    /**
     * Muestra el indicador de click en las coordenadas dadas
     * @param {number} x - Coordenada X en la pantalla
     * @param {number} y - Coordenada Y en la pantalla
     * @param {string} type - Tipo: 'click', 'double_click', 'right_click'
     */
    showClickAt(x, y, type = 'click') {
        if (!this.clickIndicator) return;

        // Ajustar clase según tipo (mantener id para CSS)
        this.clickIndicator.className = `click-type-${type}`;
        this.clickIndicator.id = 'click-indicator';
        
        // Mostrar en un toast/notificación en la app ya que no podemos posicionar
        // fuera de la ventana de Electron
        const label = type === 'double_click' ? 'Doble click'
                    : type === 'right_click' ? 'Click derecho'
                    : 'Click';
        this._showActionNotification(`${label} en (${x}, ${y})`);

        // Pedir a Electron que muestre el overlay global
        if (window.gmini && window.gmini.showClickIndicator) {
            window.gmini.showClickIndicator(x, y, type);
        }
    }

    /**
     * Muestra el efecto de captura de pantalla (local en la app)
     */
    showScreenshotEffect() {
        if (!this.screenshotOverlay) return;

        this.screenshotOverlay.classList.add('active');

        setTimeout(() => {
            this.screenshotOverlay.classList.remove('active');
        }, 800);
    }

    /**
     * Activa/desactiva el modo de ejecución visual
     */
    setExecutingMode(active) {
        this.isExecutingTask = active;
        document.body.classList.toggle('executing-task', active);
    }

    /**
     * Aviso efímero de una acción. Flota sobre el chat en su propio contenedor:
     * antes se insertaba en #messages y hacía saltar el scroll (B27).
     */
    _showActionNotification(text) {
        const host = document.getElementById('action-notifications');
        if (!host) return;
        const notification = document.createElement('div');
        notification.className = 'action-notification';
        notification.textContent = text;
        while (host.children.length >= 3) host.firstElementChild.remove();
        host.appendChild(notification);
        setTimeout(() => notification.remove(), 2600);
    }

    /** Teclas como texto: el planner manda listas (["ctrl", "c"]) o un string (B9). */
    static keysLabel(keys) {
        return Array.isArray(keys) ? keys.map(String).join(' + ') : String(keys ?? '');
    }

    /**
     * Muestra el indicador de escritura
     */
    showTypingEffect(text) {
        const value = String(text ?? '');
        this._showActionNotification(`Escribiendo: "${value.substring(0, 30)}${value.length > 30 ? '...' : ''}"`);
    }

    showKeyPress(key) {
        this._showActionNotification(`Tecla: ${OverlayEffects.keysLabel(key).toUpperCase()}`);
    }

    showHotkey(keys) {
        this._showActionNotification(`Atajo: ${OverlayEffects.keysLabel(keys).toUpperCase()}`);
    }

    showScroll(direction) {
        this._showActionNotification(`Scroll ${direction < 0 ? 'abajo' : 'arriba'}`);
    }

    showMove(x, y) {
        this._showActionNotification(`Mover a (${x}, ${y})`);
        if (window.gmini && window.gmini.showCursorBubble) {
            window.gmini.showCursorBubble(x, y);
        }
    }

    /**
     * Muestra el indicador de drag
     */
    showDrag(x, y) {
        this._showActionNotification(`Arrastrando a (${x}, ${y})`);
        if (window.gmini && window.gmini.showCursorBubble) {
            window.gmini.showCursorBubble(x, y);
        }
    }

    /**
     * Muestra el indicador de espera
     */
    showWait(seconds) {
        this._showActionNotification(`Esperando ${seconds}s...`);
    }
}

const overlayEffects = new OverlayEffects();
