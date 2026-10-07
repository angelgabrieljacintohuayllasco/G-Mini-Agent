/**
 * G-Mini Agent — Onboarding Wizard UI
 * Renders wizard steps generically from backend-driven step definitions.
 *
 * Los estilos viven en main.css bajo #onboarding-modal (antes se inyectaban
 * reglas globales .btn-* que repintaban todos los botones de la app, B13).
 */

class OnboardingWizard {
    constructor(ws) {
        this.ws = ws;
        this.modal = null;
        this._returnFocus = null;
        this._onKeydown = (e) => {
            const card = this.modal?.querySelector('.onboarding-card');
            if (card) window.gminiDom?.trapFocus(card, e);
        };
        this._bind();
    }

    _bind() {
        this.ws.on('onboarding:required', () => this._show());
        this.ws.on('onboarding:step', (data) => this._renderStep(data));
        this.ws.on('onboarding:done', (data) => this._close(data));
    }

    _emit(event, payload) {
        if (!this.ws.socket) return;
        this.ws.socket.emit(event, payload);
    }

    _show() {
        if (this.modal) return;
        this._returnFocus = document.activeElement;
        this.modal = document.createElement('div');
        this.modal.id = 'onboarding-modal';
        this.modal.innerHTML = `
            <div class="onboarding-overlay">
                <div class="onboarding-card" role="dialog" aria-modal="true"
                     aria-labelledby="onboarding-title" aria-describedby="onboarding-help">
                    <div class="onboarding-header">
                        <h2 id="onboarding-title"></h2>
                        <span id="onboarding-progress" aria-live="polite"></span>
                    </div>
                    <p id="onboarding-help"></p>
                    <div id="onboarding-fields"></div>
                    <div class="onboarding-actions">
                        <button id="onboarding-skip" class="btn-secondary" type="button">Omitir</button>
                        <button id="onboarding-next" class="btn-primary" type="button">Siguiente</button>
                        <button id="onboarding-cancel" class="btn-text" type="button">Cancelar configuración</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(this.modal);
        document.addEventListener('keydown', this._onKeydown, true);
        this._emit('onboarding:start', {});
    }

    _renderStep(step) {
        if (!this.modal) this._show();
        this._currentStep = step;
        document.getElementById('onboarding-title').textContent = step.title || '';
        document.getElementById('onboarding-help').textContent = step.help || '';
        const total = step.total_steps || '';
        document.getElementById('onboarding-progress').textContent =
            step.step_number ? `Paso ${step.step_number} de ${total}` : '';

        const container = document.getElementById('onboarding-fields');
        container.innerHTML = '';

        (step.fields || []).forEach((field, index) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'onboarding-field';
            const inputId = `onboarding-field-${index}`;

            const label = document.createElement('label');
            label.htmlFor = inputId;
            label.textContent = field.label || field.name;
            wrapper.appendChild(label);

            let input;
            if (field.type === 'select') {
                input = document.createElement('select');
                for (const opt of (field.options || [])) {
                    const o = document.createElement('option');
                    o.value = opt;
                    o.textContent = opt;
                    if (opt === field.default) o.selected = true;
                    input.appendChild(o);
                }
            } else if (field.type === 'toggle') {
                input = document.createElement('input');
                input.type = 'checkbox';
                input.checked = field.default || false;
            } else if (field.type === 'secret') {
                input = document.createElement('input');
                input.type = 'password';
                input.autocomplete = 'off';
                input.placeholder = field.optional ? '(opcional)' : 'Requerido';
            } else {
                input = document.createElement('input');
                input.type = 'text';
                input.value = field.default || '';
            }
            input.id = inputId;
            input.name = field.name;
            input.className = 'onboarding-input';
            wrapper.appendChild(input);
            container.appendChild(wrapper);
        });

        document.getElementById('onboarding-next').onclick = () => this._submit();
        document.getElementById('onboarding-skip').onclick = () => this._skip();
        document.getElementById('onboarding-cancel').onclick = () => this._cancel();

        const first = container.querySelector('.onboarding-input') || document.getElementById('onboarding-next');
        first?.focus();
    }

    _collect() {
        const value = {};
        const inputs = document.querySelectorAll('#onboarding-fields .onboarding-input');
        inputs.forEach(el => {
            if (el.type === 'checkbox') {
                value[el.name] = el.checked;
            } else {
                value[el.name] = el.value;
            }
        });
        return value;
    }

    _submit() {
        if (!this._currentStep) return;
        const value = this._collect();
        this._emit('onboarding:answer', {
            step_id: this._currentStep.id,
            value,
        });
    }

    _skip() {
        if (!this._currentStep) return;
        this._emit('onboarding:answer', {
            step_id: this._currentStep.id,
            value: { skip: true },
        });
    }

    _cancel() {
        this._emit('onboarding:cancel', {});
    }

    _close(data) {
        if (this.modal) {
            this.modal.remove();
            this.modal = null;
            document.removeEventListener('keydown', this._onKeydown, true);
        }
        const back = this._returnFocus;
        this._returnFocus = null;
        if (back && document.contains(back) && typeof back.focus === 'function') back.focus();

        if (data && data.status === 'done' && window.settingsManager) {
            // B12: no existe loadSettings(); la resincronización real es _syncFromBackend().
            Promise.resolve()
                .then(() => window.settingsManager._syncFromBackend())
                .catch((err) => console.warn('[Onboarding] No se pudo refrescar la configuración:', err));
        }
    }
}

if (typeof window !== 'undefined') {
    window.OnboardingWizard = OnboardingWizard;
}
