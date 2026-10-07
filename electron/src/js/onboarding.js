/**
 * G-Mini Agent — Asistente de configuración inicial.
 *
 * Protocolo (Socket.IO; el núcleo tiene un espejo REST en /api/onboarding/*):
 *   cliente -> núcleo: onboarding:start {rerun?}, onboarding:answer {step_id, value},
 *                      onboarding:back {}, onboarding:cancel {}
 *   núcleo -> cliente: onboarding:required {}, onboarding:step <paso>,
 *                      onboarding:done {status: "done"|"cancelled"}, onboarding:error {message}
 *
 * Un paso trae {id, title, help, step_number, total_steps, notice, fields}.
 * notice.kind "error" llega con el MISMO paso (validación): se conserva lo
 * escrito y se muestra en línea sin avanzar. "warning"/"info" llegan con el
 * paso siguiente y se muestran como aviso no bloqueante.
 *
 * Los estilos viven en main.css bajo #onboarding-modal (B13).
 */

class OnboardingWizard {
    constructor(ws) {
        this.ws = ws;
        this.modal = null;
        this.currentStep = null;
        this.busy = false;
        this._returnFocus = null;
        this._onKeydown = (e) => {
            const card = this.modal?.querySelector('.onboarding-card');
            if (card && e.key === 'Tab') window.gminiDom?.trapFocus(card, e);
        };
        ws.on('onboarding:required', () => this.start());
        ws.on('onboarding:step', (step) => this._renderStep(step));
        ws.on('onboarding:done', (data) => this._finish(data));
        ws.on('onboarding:error', (data) => this._showError(data?.message));
    }

    /** Abre el asistente y pide el primer paso. rerun=true lo repite aunque ya se hiciera. */
    start({ rerun = false } = {}) {
        this.currentStep = null;
        this._open();
        this._setBusy(true);
        this._emit('onboarding:start', rerun ? { rerun: true } : {});
    }

    _emit(event, payload) {
        // socket.io guarda en cola lo emitido sin conexión y lo envía al reconectar.
        this.ws.socket?.emit(event, payload);
    }

    _el(id) {
        return this.modal?.querySelector(`#${id}`) || null;
    }

    _open() {
        if (this.modal) return;
        this._returnFocus = document.activeElement;
        this.modal = document.createElement('div');
        this.modal.id = 'onboarding-modal';
        this.modal.innerHTML = `
            <div class="onboarding-overlay">
                <div class="onboarding-card" role="dialog" aria-modal="true"
                     aria-labelledby="onboarding-title" aria-describedby="onboarding-help">
                    <div class="onboarding-header">
                        <div class="onboarding-steps" aria-hidden="true"></div>
                        <span id="onboarding-progress"></span>
                    </div>
                    <h2 id="onboarding-title">Preparando el asistente</h2>
                    <p id="onboarding-help" hidden></p>
                    <div id="onboarding-notice" class="onboarding-notice" hidden></div>
                    <form id="onboarding-form" novalidate>
                        <div id="onboarding-fields"></div>
                        <div class="onboarding-actions">
                            <button id="onboarding-cancel" class="btn-text" type="button">Cancelar configuración</button>
                            <button id="onboarding-back" class="btn-secondary" type="button" disabled>Atrás</button>
                            <button id="onboarding-next" class="btn-primary" type="submit">Siguiente</button>
                        </div>
                    </form>
                </div>
            </div>`;
        document.body.appendChild(this.modal);
        this._el('onboarding-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this._submit();
        });
        this._el('onboarding-back').addEventListener('click', () => this._back());
        this._el('onboarding-cancel').addEventListener('click', () => this._cancel());
        document.addEventListener('keydown', this._onKeydown, true);
        this._el('onboarding-next').focus();
    }

    _close() {
        if (!this.modal) return;
        this.modal.remove();
        this.modal = null;
        this.currentStep = null;
        this.busy = false;
        document.removeEventListener('keydown', this._onKeydown, true);
        const back = this._returnFocus;
        this._returnFocus = null;
        if (back && document.contains(back) && typeof back.focus === 'function') back.focus();
    }

    /** Mientras se espera respuesta del núcleo no se puede volver a enviar. */
    _setBusy(busy) {
        this.busy = busy;
        if (!this.modal) return;
        this.modal.querySelector('.onboarding-card')?.setAttribute('aria-busy', busy ? 'true' : 'false');
        const next = this._el('onboarding-next');
        const back = this._el('onboarding-back');
        if (next) next.disabled = busy;
        if (back) back.disabled = busy || (this.currentStep?.step_number || 1) <= 1;
    }

    _renderStep(step) {
        if (!step || typeof step !== 'object') return;
        this._open();
        // Un error de validación reenvía el mismo paso: se conserva lo escrito.
        const sameStep = this.currentStep && this.currentStep.id === step.id;
        const previousValues = sameStep ? this._collect() : null;
        this.currentStep = step;

        const total = Number(step.total_steps) || 0;
        const current = Number(step.step_number) || 0;
        this._el('onboarding-title').textContent = step.title || '';
        const help = this._el('onboarding-help');
        help.textContent = step.help || '';
        help.hidden = !step.help;
        this._el('onboarding-progress').textContent = current && total ? `Paso ${current} de ${total}` : '';
        this._renderProgress(current, total);
        this._renderNotice(step.notice);
        this._renderFields(Array.isArray(step.fields) ? step.fields : [], previousValues);

        const next = this._el('onboarding-next');
        next.textContent = current && total && current >= total ? 'Terminar' : 'Siguiente';
        delete next.dataset.retry;
        this._setBusy(false);

        const isError = step.notice && step.notice.kind === 'error';
        const firstField = this.modal.querySelector('.onboarding-input');
        (isError ? (firstField || this._el('onboarding-notice')) : (firstField || next))?.focus();
    }

    _renderProgress(current, total) {
        const bar = this.modal.querySelector('.onboarding-steps');
        bar.innerHTML = '';
        for (let i = 1; i <= total; i += 1) {
            const seg = document.createElement('span');
            if (i < current) seg.className = 'is-done';
            else if (i === current) seg.className = 'is-current';
            bar.appendChild(seg);
        }
    }

    _renderNotice(notice) {
        const box = this._el('onboarding-notice');
        box.innerHTML = '';
        if (!notice || !notice.text) {
            box.hidden = true;
            box.className = 'onboarding-notice';
            box.removeAttribute('role');
            return;
        }
        const kind = ['info', 'warning', 'error'].includes(notice.kind) ? notice.kind : 'info';
        const icons = { info: 'info', warning: 'triangle-alert', error: 'circle-x' };
        box.hidden = false;
        box.className = `onboarding-notice is-${kind}`;
        // El error interrumpe (alert); los demás avisos se anuncian sin cortar.
        box.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        box.tabIndex = -1;
        box.appendChild(window.gminiDom.iconEl(icons[kind]));
        const text = document.createElement('span');
        text.textContent = String(notice.text);
        box.appendChild(text);
    }

    _renderFields(fields, previousValues) {
        const container = this._el('onboarding-fields');
        container.innerHTML = '';
        fields.forEach((field, index) => {
            const id = `onboarding-field-${index}`;
            const type = String(field.type || 'text');
            const wrapper = document.createElement('div');
            wrapper.className = `onboarding-field${type === 'toggle' ? ' is-toggle' : ''}`;
            const label = document.createElement('label');
            label.htmlFor = id;
            label.textContent = field.label || field.name;
            if (field.optional && type !== 'toggle') {
                const hint = document.createElement('span');
                hint.className = 'onboarding-optional';
                hint.textContent = 'opcional';
                label.append(' ', hint);
            }

            const input = this._buildInput(field, type);
            input.id = id;
            input.name = field.name;
            input.classList.add('onboarding-input');
            if (field.placeholder) input.placeholder = String(field.placeholder);
            const maxLength = Number(field.maxlength);
            if (maxLength > 0 && 'maxLength' in input) input.maxLength = maxLength;
            if (!field.optional && type !== 'toggle') input.setAttribute('aria-required', 'true');

            if (previousValues && Object.prototype.hasOwnProperty.call(previousValues, field.name)) {
                if (type === 'toggle') input.checked = !!previousValues[field.name];
                else input.value = previousValues[field.name];
            }

            if (type === 'toggle') wrapper.append(input, label);
            else wrapper.append(label, input);
            container.appendChild(wrapper);
        });
    }

    _buildInput(field, type) {
        if (type === 'select') {
            const select = document.createElement('select');
            // Las opciones llegan como {value, label}; se aceptan strings por compatibilidad.
            for (const opt of (field.options || [])) {
                const isObject = opt && typeof opt === 'object';
                const value = isObject ? String(opt.value ?? '') : String(opt);
                const text = isObject ? String(opt.label ?? opt.value ?? '') : String(opt);
                const option = new Option(text, value);
                if (field.default != null && value === String(field.default)) option.selected = true;
                select.appendChild(option);
            }
            return select;
        }
        if (type === 'toggle') {
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = !!field.default;
            return box;
        }
        if (type === 'textarea') {
            const area = document.createElement('textarea');
            area.rows = 4;
            area.value = field.default == null ? '' : String(field.default);
            return area;
        }
        const input = document.createElement('input');
        input.type = type === 'secret' ? 'password' : 'text';
        input.autocomplete = 'off';
        input.spellcheck = false;
        if (type !== 'secret') input.value = field.default == null ? '' : String(field.default);
        return input;
    }

    _collect() {
        const value = {};
        this.modal?.querySelectorAll('#onboarding-fields .onboarding-input').forEach((el) => {
            value[el.name] = el.type === 'checkbox' ? el.checked : el.value;
        });
        return value;
    }

    _submit() {
        const next = this._el('onboarding-next');
        if (next?.dataset.retry) {
            this.start({ rerun: true });
            return;
        }
        if (this.busy || !this.currentStep) return;
        this._setBusy(true);
        this._emit('onboarding:answer', { step_id: this.currentStep.id, value: this._collect() });
    }

    _back() {
        if (this.busy || !this.currentStep || (this.currentStep.step_number || 1) <= 1) return;
        this._setBusy(true);
        this._emit('onboarding:back', {});
    }

    _cancel() {
        // Cancelar siempre está disponible, también si el núcleo tarda en responder.
        this._setBusy(true);
        this._emit('onboarding:cancel', {});
    }

    /** Error del núcleo: se muestra en la tarjeta; nunca se cierra ni queda vacía. */
    _showError(message) {
        this._open();
        this._renderNotice({ kind: 'error', text: message || 'Algo falló en el asistente.' });
        if (!this.currentStep) {
            this._el('onboarding-title').textContent = 'No se pudo continuar con el asistente';
            this._el('onboarding-help').hidden = true;
            this._el('onboarding-fields').innerHTML = '';
            const next = this._el('onboarding-next');
            next.textContent = 'Reintentar';
            next.dataset.retry = '1';
        }
        this._setBusy(false);
        this._el('onboarding-notice')?.focus();
    }

    _finish(data) {
        const completed = data?.status === 'done';
        this._close();
        if (!completed) return;
        if (typeof chatManager !== 'undefined') chatManager._toast('Configuración inicial completada.');
        window.gminiIdentity?.refreshFromBackend();
        if (window.settingsManager) {
            // No existe loadSettings(): la resincronización real es _syncFromBackend() (B12).
            Promise.resolve()
                .then(() => window.settingsManager._syncFromBackend())
                .catch((err) => console.warn('[Onboarding] No se pudo refrescar la configuración:', err));
        }
    }
}

if (typeof window !== 'undefined') {
    window.OnboardingWizard = OnboardingWizard;
}
