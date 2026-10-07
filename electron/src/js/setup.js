/**
 * G-Mini Agent — Ventana de preparación: pinta el estado que manda el main.
 */
(function () {
    'use strict';

    const api = window.gminiSetup;
    const stepsEl = document.getElementById('steps');
    const template = document.getElementById('step-template');
    const track = document.getElementById('track');
    const problem = document.getElementById('problem');
    const problemText = document.getElementById('problem-text');
    const tail = document.getElementById('tail');
    const rows = new Map();

    function row(step) {
        if (rows.has(step.id)) return rows.get(step.id);
        const node = template.content.firstElementChild.cloneNode(true);
        node.querySelector('.step-title').textContent = step.title;
        stepsEl.appendChild(node);
        rows.set(step.id, node);
        return node;
    }

    function render(state) {
        if (!state) return;
        let running = false;
        let failed = false;
        for (const step of state.steps) {
            const node = row(step);
            node.className = `step is-${step.status}`;
            node.querySelector('.step-note').textContent = step.note || step.detail;
            node.toggleAttribute('aria-current', step.status === 'running');
            running = running || step.status === 'running';
            failed = failed || step.status === 'failed';
        }
        const allDone = state.steps.every((s) => s.status === 'done');
        track.className = `track${running ? ' is-busy' : ''}${allDone ? ' is-done' : ''}${failed ? ' is-failed' : ''}`;

        problem.hidden = !state.error;
        problemText.textContent = state.error || '';
        tail.textContent = (state.tail || []).join('\n');
        if (state.error) document.getElementById('details').open = true;
    }

    document.getElementById('close').addEventListener('click', () => api.quit());
    document.getElementById('quit').addEventListener('click', () => api.quit());
    document.getElementById('open-log').addEventListener('click', () => api.openLog());
    document.getElementById('retry').addEventListener('click', () => {
        problem.hidden = true;
        api.retry();
    });

    api.onState(render);
    api.getState().then(render);
}());
