// Iconos SVG inline (reemplazan emojis para una UI consistente y profesional).
// Namespaced en un objeto para no colisionar con consts de otros scripts.
const CODE_ICONS = {
    trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    pin: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px"><line x1="12" y1="17" x2="12" y2="22"/><path d="M9 4V2h6v2l-1 6 2 2v3H8v-3l2-2z"/></svg>',
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#4caf50" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    cross: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#f44336" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
};

class CodeManager {
    constructor() {
        this.apiBase = 'http://127.0.0.1:8765/api';
        this.panel = document.getElementById('code-panel');
        this.panelSubtitle = document.getElementById('code-panel-subtitle');
        this.btnToggle = document.getElementById('btn-toggle-code');
        this.btnClose = document.getElementById('btn-close-code');
        this.btnRefresh = document.getElementById('btn-refresh-code');
        this.btnTabWorkspace = document.getElementById('btn-code-tab-workspace');
        this.btnTabScheduler = document.getElementById('btn-code-tab-scheduler');
        this.btnTabCanvas = document.getElementById('btn-code-tab-canvas');
        this.btnTabSecurity = document.getElementById('btn-code-tab-security');
        this.btnTabAnalytics = document.getElementById('btn-code-tab-analytics');
        this.workspaceView = document.getElementById('code-view-workspace');
        this.schedulerView = document.getElementById('code-view-scheduler');
        this.canvasView = document.getElementById('code-view-canvas');
        this.securityView = document.getElementById('code-view-security');
        this.analyticsView = document.getElementById('code-view-analytics');
        this.btnLoadWorkspace = document.getElementById('btn-load-code-workspace');
        this.btnUseFileContext = document.getElementById('btn-use-file-context');
        this.workspaceInput = document.getElementById('code-workspace-path');
        this.statusEl = document.getElementById('code-status');
        this.snapshotEl = document.getElementById('code-snapshot-summary');
        this.changedFilesEl = document.getElementById('code-changed-files');
        this.fileListEl = document.getElementById('code-file-list');
        this.currentFileLabelEl = document.getElementById('code-current-file-label');
        this.fileContentEl = document.getElementById('code-file-content');
        this.outlineEl = document.getElementById('code-outline');
        this.schedulerStatusEl = document.getElementById('scheduler-status');
        this.schedulerSummaryEl = document.getElementById('scheduler-summary');
        this.schedulerCurrentStateEl = document.getElementById('scheduler-current-state');
        this.costSummaryEl = document.getElementById('cost-summary');
        this.costCurrentStateEl = document.getElementById('cost-current-state');
        this.costWeeklySummaryEl = document.getElementById('cost-weekly-summary');
        this.costWeeklyBreakdownEl = document.getElementById('cost-weekly-breakdown');
        this.gatewaySummaryEl = document.getElementById('gateway-summary');
        this.gatewayOutboxEl = document.getElementById('gateway-outbox');
        this.schedulerJobListEl = document.getElementById('scheduler-job-list');
        this.schedulerRunsEl = document.getElementById('scheduler-runs');
        this.schedulerCheckpointsEl = document.getElementById('scheduler-checkpoints');
        this.costEventsEl = document.getElementById('cost-events');
        this.schedulerFormMetaEl = document.getElementById('scheduler-form-meta');
        this.btnRefreshScheduler = document.getElementById('btn-refresh-scheduler');
        this.btnNewSchedulerJob = document.getElementById('btn-new-scheduler-job');
        this.btnSaveSchedulerJob = document.getElementById('btn-save-scheduler-job');
        this.btnRunSchedulerJob = document.getElementById('btn-run-scheduler-job');
        this.btnTriggerSchedulerJob = document.getElementById('btn-trigger-scheduler-job');
        this.btnDeleteSchedulerJob = document.getElementById('btn-delete-scheduler-job');
        this.btnClearSchedulerJob = document.getElementById('btn-clear-scheduler-job');
        this.schedulerNameInput = document.getElementById('scheduler-job-name');
        this.schedulerTaskTypeSelect = document.getElementById('scheduler-task-type');
        this.schedulerTriggerTypeSelect = document.getElementById('scheduler-trigger-type');
        this.schedulerEnabledCheckbox = document.getElementById('scheduler-enabled');
        this.schedulerIntervalInput = document.getElementById('scheduler-interval-seconds');
        this.schedulerCronInput = document.getElementById('scheduler-cron-expression');
        this.schedulerEventNameInput = document.getElementById('scheduler-event-name');
        this.schedulerWebhookPathInput = document.getElementById('scheduler-webhook-path');
        this.schedulerWebhookSecretInput = document.getElementById('scheduler-webhook-secret');
        this.schedulerHeartbeatKeyInput = document.getElementById('scheduler-heartbeat-key');
        this.schedulerHeartbeatIntervalSecondsInput = document.getElementById('scheduler-heartbeat-interval-seconds');
        this.schedulerMaxRetriesInput = document.getElementById('scheduler-max-retries');
        this.schedulerRetryBackoffSecondsInput = document.getElementById('scheduler-retry-backoff-seconds');
        this.schedulerRetryBackoffMultiplierInput = document.getElementById('scheduler-retry-backoff-multiplier');
        this.schedulerPayloadInput = document.getElementById('scheduler-payload');

        // Canvas DOM refs
        this.canvasStatusEl = document.getElementById('canvas-status');
        this.canvasListEl = document.getElementById('canvas-list');
        this.canvasRenderArea = document.getElementById('canvas-render-area');
        this.canvasCurrentTitle = document.getElementById('canvas-current-title');
        this.canvasVersionsListEl = document.getElementById('canvas-versions-list');
        this.btnRefreshCanvas = document.getElementById('btn-refresh-canvas');
        this.btnNewCanvas = document.getElementById('btn-new-canvas');
        this.selectCanvasType = document.getElementById('select-canvas-type');
        this.btnPinCanvas = document.getElementById('btn-pin-canvas');
        this.btnCanvasVersions = document.getElementById('btn-canvas-versions');
        this.btnDeleteCanvas = document.getElementById('btn-delete-canvas');

        // Security DOM refs
        this.rbacUsersListEl = document.getElementById('rbac-users-list');
        this.rbacPoliciesListEl = document.getElementById('rbac-policies-list');
        this.ethicalRestrictionsListEl = document.getElementById('ethical-restrictions-list');
        this.auditStatsSummaryEl = document.getElementById('audit-stats-summary');
        this.auditRecentListEl = document.getElementById('audit-recent-list');
        this.sandboxStatusInfoEl = document.getElementById('sandbox-status-info');
        this.rateLimitsInfoEl = document.getElementById('rate-limits-info');
        this.btnRefreshSecurity = document.getElementById('btn-refresh-security');
        this.rbacNewUserIdInput = document.getElementById('rbac-new-user-id');
        this.rbacNewUserNameInput = document.getElementById('rbac-new-user-name');
        this.rbacNewUserRoleSelect = document.getElementById('rbac-new-user-role');
        this.btnAddRbacUser = document.getElementById('btn-add-rbac-user');
        this.btnExportAuditJson = document.getElementById('btn-export-audit-json');
        this.btnExportAuditCsv = document.getElementById('btn-export-audit-csv');

        // Analytics DOM refs
        this.analyticsDashboardSummaryEl = document.getElementById('analytics-dashboard-summary');
        this.analyticsTokensSummaryEl = document.getElementById('analytics-tokens-summary');
        this.analyticsTokensByProviderEl = document.getElementById('analytics-tokens-by-provider');
        this.analyticsTimeDistributionEl = document.getElementById('analytics-time-distribution');
        this.analyticsErrorsListEl = document.getElementById('analytics-errors-list');
        this.analyticsTimelineEl = document.getElementById('analytics-timeline');
        this.analyticsWeeklyReportEl = document.getElementById('analytics-weekly-report');
        this.analyticsPeriodSelect = document.getElementById('analytics-period');
        this.btnRefreshAnalytics = document.getElementById('btn-refresh-analytics');
        this.btnWeeklyReport = document.getElementById('btn-weekly-report');
        this.goalsListEl = document.getElementById('goals-list');
        this.goalNewTitleInput = document.getElementById('goal-new-title');
        this.goalNewDeadlineInput = document.getElementById('goal-new-deadline');
        this.btnAddGoal = document.getElementById('btn-add-goal');
        this.dagListEl = document.getElementById('dag-list');

        this.currentWorkspaceRoot = '';
        this.currentDirectoryPath = '';
        this.currentFile = null;
        this.workspaceLoaded = false;
        this.panelOpen = false;
        this.activeTab = 'workspace';
        this.schedulerLoaded = false;
        this.schedulerJobs = [];
        this.schedulerRuns = [];
        this.schedulerCheckpoints = [];
        this.schedulerRecovery = null;
        this.costSummary = null;
        this.costWeeklyReport = null;
        this.costEvents = [];
        this.gatewayStatus = null;
        this.gatewayOutbox = [];
        this.selectedJobId = '';

        // Canvas state
        this.canvasLoaded = false;
        this.canvases = [];
        this.selectedCanvasId = '';
        this.canvasVersions = [];

        // Security state
        this.securityLoaded = false;
        this.rbacUsers = [];
        this.rbacPolicies = [];
        this.ethicalRestrictions = [];
        this.auditStats = null;
        this.auditRecent = [];
        this.sandboxStatus = null;
        this.rateLimits = null;

        // Analytics state
        this.analyticsLoaded = false;
        this.analyticsDashboard = null;
        this.analyticsTokens = null;
        this.analyticsErrors = [];
        this.analyticsTimeline = [];
        this.analyticsTimeDistribution = [];
        this.goals = [];
        this.dags = [];
    }

    init() {
        this.btnToggle?.addEventListener('click', () => this.togglePanel());
        this.btnClose?.addEventListener('click', () => this.togglePanel(false));
        this.btnRefresh?.addEventListener('click', () => this.refreshActiveView());
        this.btnTabWorkspace?.addEventListener('click', () => this.setActiveTab('workspace'));
        this.btnTabScheduler?.addEventListener('click', () => this.setActiveTab('scheduler'));
        this.btnTabCanvas?.addEventListener('click', () => this.setActiveTab('canvas'));
        this.btnTabSecurity?.addEventListener('click', () => this.setActiveTab('security'));
        this.btnTabAnalytics?.addEventListener('click', () => this.setActiveTab('analytics'));
        this.btnLoadWorkspace?.addEventListener('click', () => this.loadWorkspace(this.workspaceInput?.value || ''));
        this.btnUseFileContext?.addEventListener('click', () => this.useCurrentFileInChat());
        this.workspaceInput?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.loadWorkspace(this.workspaceInput.value || '');
            }
        });
        this.fileListEl?.addEventListener('click', (event) => this.handleFileListClick(event));
        this.changedFilesEl?.addEventListener('click', (event) => this.handleChangedFileClick(event));
        this.btnRefreshScheduler?.addEventListener('click', () => this.loadScheduler({ preserveSelection: true }));
        this.btnNewSchedulerJob?.addEventListener('click', () => this.clearSchedulerForm(true));
        this.btnSaveSchedulerJob?.addEventListener('click', () => this.saveSchedulerJob());
        this.btnRunSchedulerJob?.addEventListener('click', () => this.runSelectedSchedulerJob());
        this.btnTriggerSchedulerJob?.addEventListener('click', () => this.triggerSelectedSchedulerJob());
        this.btnDeleteSchedulerJob?.addEventListener('click', () => this.deleteSelectedSchedulerJob());
        this.btnClearSchedulerJob?.addEventListener('click', () => this.clearSchedulerForm(true));
        this.schedulerJobListEl?.addEventListener('click', (event) => this.handleSchedulerJobClick(event));
        this.schedulerTriggerTypeSelect?.addEventListener('change', () => this.syncSchedulerTriggerInputs());
        // Canvas listeners
        this.btnRefreshCanvas?.addEventListener('click', () => this.loadCanvas());
        this.btnNewCanvas?.addEventListener('click', () => this.createCanvas());
        this.selectCanvasType?.addEventListener('change', () => this.loadCanvas());
        this.canvasListEl?.addEventListener('click', (event) => this.handleCanvasListClick(event));
        this.btnPinCanvas?.addEventListener('click', () => this.togglePinCanvas());
        this.btnDeleteCanvas?.addEventListener('click', () => this.deleteSelectedCanvas());
        this.btnCanvasVersions?.addEventListener('click', () => this.loadCanvasVersions());
        // Security listeners
        this.btnRefreshSecurity?.addEventListener('click', () => this.loadSecurity());
        this.btnAddRbacUser?.addEventListener('click', () => this.addRbacUser());
        this.rbacUsersListEl?.addEventListener('click', (event) => this.handleRbacUserClick(event));
        this.rbacPoliciesListEl?.addEventListener('click', (event) => this.handleRbacPolicyClick(event));
        this.btnExportAuditJson?.addEventListener('click', () => this.exportAudit('json'));
        this.btnExportAuditCsv?.addEventListener('click', () => this.exportAudit('csv'));
        // Analytics listeners
        this.btnRefreshAnalytics?.addEventListener('click', () => this.loadAnalytics());
        this.analyticsPeriodSelect?.addEventListener('change', () => this.loadAnalytics());
        this.btnWeeklyReport?.addEventListener('click', () => this.generateWeeklyReport());
        this.btnAddGoal?.addEventListener('click', () => this.addGoal());
        this.goalsListEl?.addEventListener('click', (event) => this.handleGoalClick(event));
        this.updateTabUi();
        this.updateSchedulerFormLockState();
    }

    async togglePanel(forceOpen) {
        const nextOpen = typeof forceOpen === 'boolean' ? forceOpen : this.panel.classList.contains('collapsed');
        this.panelOpen = nextOpen;
        this.panel.classList.toggle('collapsed', !nextOpen);
        if (nextOpen) await this.loadActiveView();
    }

    async setActiveTab(tab) {
        const validTabs = new Set(['workspace', 'scheduler', 'canvas', 'security', 'analytics']);
        this.activeTab = validTabs.has(tab) ? tab : 'workspace';
        this.updateTabUi();
        if (this.panelOpen) await this.loadActiveView();
    }

    updateTabUi() {
        const tab = this.activeTab;
        const tabMap = {
            workspace: { btn: this.btnTabWorkspace, view: this.workspaceView },
            scheduler: { btn: this.btnTabScheduler, view: this.schedulerView },
            canvas: { btn: this.btnTabCanvas, view: this.canvasView },
            security: { btn: this.btnTabSecurity, view: this.securityView },
            analytics: { btn: this.btnTabAnalytics, view: this.analyticsView },
        };
        for (const [key, { btn, view }] of Object.entries(tabMap)) {
            btn?.classList.toggle('is-active', key === tab);
            view?.classList.toggle('is-active', key === tab);
        }
        const subtitles = {
            workspace: 'Codigo local y contexto del proyecto dentro de G-Mini',
            scheduler: 'Scheduler persistente para skills y tools MCP',
            canvas: 'Dashboards interactivos en tiempo real',
            security: 'RBAC, auditoria, sandbox y restricciones eticas',
            analytics: 'Metricas, costos, objetivos y DAG planner',
        };
        if (this.panelSubtitle) this.panelSubtitle.textContent = subtitles[tab] || '';
        if (this.btnRefresh) {
            const labels = { workspace: 'Recargar', scheduler: 'Jobs', canvas: 'Canvases', security: 'Seguridad', analytics: 'Analytics' };
            this.btnRefresh.textContent = labels[tab] || 'Recargar';
            this.btnRefresh.title = `Recargar ${labels[tab] || 'vista'}`;
        }
    }

    async loadActiveView() {
        if (this.activeTab === 'scheduler') { await this.loadScheduler({ preserveSelection: true }); return; }
        if (this.activeTab === 'canvas') { await this.loadCanvas(); return; }
        if (this.activeTab === 'security') { await this.loadSecurity(); return; }
        if (this.activeTab === 'analytics') { await this.loadAnalytics(); return; }
        if (!this.workspaceLoaded) await this.loadWorkspace(this.workspaceInput?.value || '');
    }

    async refreshActiveView() {
        if (this.activeTab === 'scheduler') { await this.loadScheduler({ preserveSelection: true }); return; }
        if (this.activeTab === 'canvas') { await this.loadCanvas(); return; }
        if (this.activeTab === 'security') { await this.loadSecurity(); return; }
        if (this.activeTab === 'analytics') { await this.loadAnalytics(); return; }
        await this.loadWorkspace(this.workspaceInput?.value || this.currentWorkspaceRoot || '');
    }

    async loadWorkspace(path) {
        this.setWorkspaceStatus('Cargando workspace...');
        try {
            const snapshot = await this.fetchJson('/workspace/snapshot', { path: path || '', max_entries: 80, include_git: true });
            this.workspaceLoaded = true;
            this.currentWorkspaceRoot = snapshot.project_root || '';
            this.currentDirectoryPath = this.currentWorkspaceRoot;
            if (this.workspaceInput) this.workspaceInput.value = this.currentWorkspaceRoot;
            this.renderSnapshot(snapshot);
            this.renderChangedFiles(snapshot.git || null);
            await this.loadDirectory(this.currentWorkspaceRoot);
            this.setWorkspaceStatus(`Workspace listo: ${snapshot.relative_project_root || snapshot.project_root}`);
        } catch (error) {
            this.renderSnapshot(null);
            this.renderChangedFiles(null);
            this.renderDirectory(null, []);
            this.renderFile(null, [], []);
            this.setWorkspaceStatus(error.message || 'No se pudo cargar el workspace.', true);
        }
    }

    async loadDirectory(path) {
        if (!path) return;
        const listing = await this.fetchJson('/workspace/list', { path, include_dirs: true, recursive: false, include_hidden: false, max_results: 300 });
        this.currentDirectoryPath = listing.base_path || path;
        this.renderDirectory(listing.base_path || path, listing.entries || []);
    }

    async openFile(path) {
        if (!path) return;
        this.setWorkspaceStatus(`Leyendo ${path}...`);
        try {
            const [fileData, outlineData, relatedData] = await Promise.all([
                this.fetchJson('/workspace/file', { path, start_line: 1, max_lines: 400 }),
                this.fetchJson('/workspace/code/outline', { path, max_symbols: 80 }).catch(() => ({ symbols: [] })),
                this.fetchJson('/workspace/code/related', { path, max_results: 12 }).catch(() => ({ related_files: [] })),
            ]);
            this.currentFile = fileData;
            this.renderFile(fileData, outlineData.symbols || [], relatedData.related_files || []);
            this.setWorkspaceStatus(`Archivo cargado: ${fileData.relative_path || fileData.path}`);
        } catch (error) {
            this.renderFile(null, [], []);
            this.setWorkspaceStatus(error.message || 'No se pudo leer el archivo.', true);
        }
    }

    renderSnapshot(snapshot) {
        if (!snapshot) {
            this.snapshotEl.innerHTML = '<div class="code-empty-state">Sin snapshot disponible.</div>';
            return;
        }
        const gitBranch = snapshot.git?.branch || 'Sin git';
        const kinds = Array.isArray(snapshot.detected_kinds) ? snapshot.detected_kinds.join(', ') : '-';
        const markers = Array.isArray(snapshot.markers) ? snapshot.markers.join(', ') : '-';
        const cards = [
            ['Root', this.escapeHtml(snapshot.relative_project_root || snapshot.project_root || '-')],
            ['Tipos', this.escapeHtml(kinds || '-')],
            ['Branch', this.escapeHtml(gitBranch || '-')],
            ['Markers', this.escapeHtml(markers || '-')],
            ['Entradas', this.escapeHtml(String(snapshot.entry_count || 0))],
        ];
        this.snapshotEl.innerHTML = cards.map(([label, value]) => `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${value}</div></div>`).join('');
    }

    renderChangedFiles(gitStatus) {
        if (!gitStatus || !gitStatus.is_repo) {
            this.changedFilesEl.innerHTML = '<div class="code-empty-state">Sin cambios Git o no es un repo.</div>';
            return;
        }
        const entries = Array.isArray(gitStatus.entries) ? gitStatus.entries.slice(0, 30) : [];
        if (entries.length === 0) {
            this.changedFilesEl.innerHTML = '<div class="code-empty-state">Working tree limpia.</div>';
            return;
        }
        this.changedFilesEl.innerHTML = entries.map((entry) => `
            <button class="code-list-item code-list-item-compact" data-path="${this.escapeHtml(entry.path || '')}" data-kind="git">
                <span class="code-list-badge">${this.escapeHtml(entry.status || '--')}</span>
                <span class="code-list-text">${this.escapeHtml(entry.path || '')}</span>
            </button>
        `).join('');
    }

    renderDirectory(basePath, entries) {
        if (!basePath) {
            this.fileListEl.innerHTML = '<div class="code-empty-state">Sin archivos cargados.</div>';
            return;
        }
        const normalizedEntries = Array.isArray(entries) ? entries.slice() : [];
        normalizedEntries.sort((left, right) => {
            if (Boolean(left.is_dir) !== Boolean(right.is_dir)) return left.is_dir ? -1 : 1;
            return String(left.name || '').localeCompare(String(right.name || ''));
        });
        const items = [];
        if (this.currentWorkspaceRoot && basePath !== this.currentWorkspaceRoot) {
            items.push(`<button class="code-list-item" data-path="${this.escapeHtml(this.getParentPath(basePath))}" data-kind="dir"><span class="code-list-icon">..</span><span class="code-list-text">Subir un nivel</span></button>`);
        }
        items.push(...normalizedEntries.map((entry) => `
            <button class="code-list-item ${entry.is_dir ? 'is-dir' : ''}" data-path="${this.escapeHtml(entry.path || '')}" data-kind="${entry.is_dir ? 'dir' : 'file'}">
                <span class="code-list-icon">${entry.is_dir ? '[D]' : '[F]'}</span>
                <span class="code-list-text">${this.escapeHtml(entry.name || entry.relative_path || entry.path || '')}</span>
            </button>
        `));
        this.fileListEl.innerHTML = items.join('') || '<div class="code-empty-state">No se encontraron archivos.</div>';
    }

    renderFile(fileData, symbols, relatedFiles) {
        if (!fileData) {
            this.currentFileLabelEl.textContent = 'Archivo';
            this.fileContentEl.textContent = 'Selecciona un archivo para ver su contenido.';
            this.outlineEl.innerHTML = '';
            return;
        }
        this.currentFileLabelEl.textContent = fileData.relative_path || fileData.path || 'Archivo';
        const content = String(fileData.content || '');
        const startLine = Number(fileData.start_line || 1);
        const numberedLines = content.split('\n').map((line, index) => `${String(startLine + index).padStart(4, ' ')} | ${line}`).join('\n');
        this.fileContentEl.innerHTML = this.escapeHtml(numberedLines || '(archivo vacio)');
        const symbolChips = Array.isArray(symbols) ? symbols.slice(0, 10).map((symbol) => `<span class="code-chip">${this.escapeHtml(symbol.kind || 'symbol')}: ${this.escapeHtml(symbol.name || '')}</span>`) : [];
        const relatedChips = Array.isArray(relatedFiles) ? relatedFiles.slice(0, 6).map((file) => `<button class="code-chip code-chip-action" data-path="${this.escapeHtml(file.path || '')}" data-kind="file">Rel: ${this.escapeHtml(file.relative_path || file.path || '')}</button>`) : [];
        this.outlineEl.innerHTML = [...symbolChips, ...relatedChips].join('') || '<span class="code-empty-state">Sin simbolos detectados.</span>';
        this.outlineEl.querySelectorAll('[data-kind="file"]').forEach((button) => button.addEventListener('click', () => this.openFile(button.dataset.path || '')));
    }

    async handleFileListClick(event) {
        const button = event.target.closest('[data-path]');
        if (!button) return;
        const kind = button.dataset.kind || '';
        const path = button.dataset.path || '';
        if (kind === 'dir') {
            await this.loadDirectory(path);
            return;
        }
        await this.openFile(path);
    }

    async handleChangedFileClick(event) {
        const button = event.target.closest('[data-path]');
        if (!button) return;
        await this.openFile(this.resolveWorkspacePath(button.dataset.path || ''));
    }

    useCurrentFileInChat() {
        if (!this.currentFile) {
            this.setWorkspaceStatus('Selecciona un archivo antes de enviarlo al chat.', true);
            return;
        }
        const path = this.currentFile.relative_path || this.currentFile.path || 'archivo';
        const prompt = [
            'Usa este contexto de codigo local para ayudarme desde G-Mini.',
            `Archivo: ${path}`,
            `Lineas: ${this.currentFile.start_line}-${this.currentFile.end_line}`,
            'Contenido:',
            '```',
            String(this.currentFile.content || ''),
            '```',
        ].join('\n');
        if (window.gminiComposer?.appendText) {
            window.gminiComposer.appendText(prompt);
            this.setWorkspaceStatus(`Contexto agregado al chat: ${path}`);
        }
    }

    async loadScheduler(options = {}) {
        const preserveSelection = options.preserveSelection !== false;
        this.setSchedulerStatus('Cargando jobs programados...');
        try {
            const [jobsData, recoveryData, costSummaryData, costWeeklyReportData, costEventsData, gatewayStatusData, gatewayOutboxData] = await Promise.all([
                this.fetchJson('/scheduler/jobs'),
                this.fetchJson('/scheduler/recovery').catch(() => ({ checked_at: null, interrupted_runs: 0, rescheduled_jobs: 0, retry_scheduled_jobs: 0, recovered_run_ids: [] })),
                this.fetchJson('/costs/summary').catch(() => null),
                this.fetchJson('/costs/reports/weekly').catch(() => null),
                this.fetchJson('/costs/events', { limit: 30 }).catch(() => ({ events: [] })),
                this.fetchJson('/gateway/status').catch(() => null),
                this.fetchJson('/gateway/outbox', { limit: 20 }).catch(() => ({ notifications: [] })),
            ]);
            this.schedulerLoaded = true;
            this.schedulerJobs = Array.isArray(jobsData.jobs) ? jobsData.jobs : [];
            this.schedulerRecovery = recoveryData || null;
            this.costSummary = costSummaryData || null;
            this.costWeeklyReport = costWeeklyReportData || null;
            this.costEvents = Array.isArray(costEventsData?.events) ? costEventsData.events : [];
            this.gatewayStatus = gatewayStatusData || null;
            this.gatewayOutbox = Array.isArray(gatewayOutboxData?.notifications) ? gatewayOutboxData.notifications : [];
            const keepSelection = preserveSelection && this.selectedJobId && this.schedulerJobs.some((job) => job.job_id === this.selectedJobId);
            this.renderSchedulerSummary();
            this.renderCostSummary();
            this.renderCostCurrentState();
            this.renderCostWeeklyReport();
            this.renderCostEvents();
            this.renderGatewaySummary();
            this.renderGatewayOutbox();
            this.renderSchedulerJobs();
            if (keepSelection) this.populateSchedulerForm(this.getSchedulerJob(this.selectedJobId));
            else if (this.selectedJobId) this.clearSchedulerForm(false);
            const activeJobId = keepSelection ? this.selectedJobId : '';
            await Promise.all([
                this.loadSchedulerRuns(activeJobId),
                this.loadSchedulerCheckpoints(activeJobId),
            ]);
            this.renderSchedulerCurrentState();
            this.setSchedulerStatus(
                `Scheduler listo: ${this.schedulerJobs.length} job(s) detectado(s). Recuperados al iniciar: ${Number(this.schedulerRecovery?.interrupted_runs || 0)}.`
            );
        } catch (error) {
            this.schedulerJobs = [];
            this.schedulerRuns = [];
            this.schedulerCheckpoints = [];
            this.schedulerRecovery = null;
            this.costSummary = null;
            this.costWeeklyReport = null;
            this.costEvents = [];
            this.gatewayStatus = null;
            this.gatewayOutbox = [];
            this.renderSchedulerSummary();
            this.renderSchedulerCurrentState();
            this.renderCostSummary();
            this.renderCostCurrentState();
            this.renderCostWeeklyReport();
            this.renderGatewaySummary();
            this.renderGatewayOutbox();
            this.renderSchedulerJobs();
            this.renderSchedulerRuns();
            this.renderSchedulerCheckpoints();
            this.renderCostEvents();
            this.setSchedulerStatus(error.message || 'No se pudieron cargar los jobs.', true);
        }
    }

    async refreshGateway() {
        if (this.activeTab !== 'scheduler') return;
        try {
            const [gatewayStatusData, gatewayOutboxData] = await Promise.all([
                this.fetchJson('/gateway/status').catch(() => null),
                this.fetchJson('/gateway/outbox', { limit: 20 }).catch(() => ({ notifications: [] })),
            ]);
            this.gatewayStatus = gatewayStatusData || null;
            this.gatewayOutbox = Array.isArray(gatewayOutboxData?.notifications) ? gatewayOutboxData.notifications : [];
            this.renderGatewaySummary();
            this.renderGatewayOutbox();
        } catch (error) {
            // refresh suave, sin bloquear la UI
        }
    }

    async loadSchedulerRuns(jobId = '') {
        try {
            const data = await this.fetchJson('/scheduler/runs', { job_id: jobId || '', limit: 30 });
            this.schedulerRuns = Array.isArray(data.runs) ? data.runs : [];
            this.renderSchedulerRuns();
        } catch (error) {
            this.schedulerRuns = [];
            this.renderSchedulerRuns();
            this.setSchedulerStatus(error.message || 'No se pudo cargar el historial.', true);
        }
    }

    async loadSchedulerCheckpoints(jobId = '') {
        try {
            const data = await this.fetchJson('/scheduler/checkpoints', { job_id: jobId || '', limit: 60 });
            this.schedulerCheckpoints = Array.isArray(data.checkpoints) ? data.checkpoints : [];
            if (data.recovery) this.schedulerRecovery = data.recovery;
            this.renderSchedulerCurrentState();
            this.renderSchedulerCheckpoints();
        } catch (error) {
            this.schedulerCheckpoints = [];
            this.renderSchedulerCurrentState();
            this.renderSchedulerCheckpoints();
            this.setSchedulerStatus(error.message || 'No se pudieron cargar los checkpoints.', true);
        }
    }

    renderSchedulerSummary() {
        const jobs = Array.isArray(this.schedulerJobs) ? this.schedulerJobs : [];
        const recovery = this.schedulerRecovery || {};
        const cards = [
            ['Total', jobs.length],
            ['Enabled', jobs.filter((job) => job.enabled).length],
            ['Interval', jobs.filter((job) => job.trigger_type === 'interval').length],
            ['Cron', jobs.filter((job) => job.trigger_type === 'cron').length],
            ['Signals', jobs.filter((job) => ['heartbeat', 'event', 'webhook'].includes(job.trigger_type)).length],
            ['Errores', jobs.filter((job) => String(job.last_error || '').trim()).length],
            ['Retrying', jobs.filter((job) => Number(job.retry_attempt || 0) > 0).length],
            ['Recovered', Number(recovery.interrupted_runs || 0)],
            ['Requeued', Number(recovery.rescheduled_jobs || 0)],
            ['Last check', this.formatDateTime(recovery.checked_at)],
        ];
        this.schedulerSummaryEl.innerHTML = cards.map(([label, value]) => `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`).join('');
    }

    renderSchedulerCurrentState() {
        if (!this.schedulerCurrentStateEl) return;
        const selectedJob = this.getSchedulerJob(this.selectedJobId);
        const latestRun = Array.isArray(this.schedulerRuns) && this.schedulerRuns.length > 0 ? this.schedulerRuns[0] : null;
        const latestCheckpoint = Array.isArray(this.schedulerCheckpoints) && this.schedulerCheckpoints.length > 0 ? this.schedulerCheckpoints[0] : null;
        const stateCards = [
            ['Job', selectedJob?.name || 'Sin seleccion'],
            ['Estado', latestRun?.status || 'idle'],
            ['Progreso', latestCheckpoint ? `${Math.round(Number(latestCheckpoint.progress || 0))}%` : '-'],
            ['Checkpoint', latestCheckpoint?.checkpoint_type || '-'],
            ['Ultimo log', latestCheckpoint?.message || (selectedJob?.last_error || '-')],
            ['Proxima ejecucion', this.formatDateTime(selectedJob?.next_run_at)],
            ['Ultima señal', this.formatDateTime(selectedJob?.last_signal_at)],
            ['Recovery', `${Number(this.schedulerRecovery?.interrupted_runs || 0)} run(s)`],
        ];
        this.schedulerCurrentStateEl.innerHTML = stateCards
            .map(([label, value]) => `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`)
            .join('');
    }

    renderCostSummary() {
        if (!this.costSummaryEl) return;
        const summary = this.costSummary;
        if (!summary) {
            this.costSummaryEl.innerHTML = '<div class="code-empty-state">Sin datos de costo todavía.</div>';
            return;
        }
        const session = summary.current_session || {};
        const modeUsage = summary.current_mode_usage || {};
        const currentWorker = summary.current_worker || {};
        const today = summary.today || {};
        const month = summary.month || {};
        const cards = [
            ['Sesion', this.formatUsd(session.total_cost_usd)],
            ['Modo actual', this.formatUsd(modeUsage.total_cost_usd)],
            ['Worker actual', this.formatUsd(currentWorker.total_cost_usd)],
            ['Hoy', this.formatUsd(today.total_cost_usd)],
            ['Mes', this.formatUsd(month.total_cost_usd)],
            ['Eventos', String(Number(session.event_count || 0))],
            ['Tokens sesion', this.formatTokenCount(session.total_tokens)],
            ['Modelos con precio', String(Number(summary.configured_models || 0))],
            ['Estimados', String(Number(session.estimated_events || 0))],
            ['Sin precio', String(Number(session.unpriced_events || 0))],
        ];
        this.costSummaryEl.innerHTML = cards
            .map(([label, value]) => `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`)
            .join('');
    }

    renderCostCurrentState() {
        if (!this.costCurrentStateEl) return;
        const summary = this.costSummary;
        if (!summary) {
            this.costCurrentStateEl.innerHTML = '<div class="code-empty-state">Sin estado de presupuesto todavía.</div>';
            return;
        }
        const budget = summary.budget_status || {};
        const alerts = Array.isArray(summary.alerts) ? summary.alerts : [];
        const cards = [
            ['Monitor', summary.enabled ? 'activo' : 'desactivado'],
            ['Tarea', this.formatBudgetBucket(budget.session)],
            ['Modo', this.formatBudgetBucket(budget.mode)],
            ['Worker', this.formatBudgetBucket(budget.current_worker)],
            ['Diario', this.formatBudgetBucket(budget.daily)],
            ['Mensual', this.formatBudgetBucket(budget.monthly)],
            ['Umbral', `${Number(budget.warning_threshold_percent || 0)}%`],
            ['Límite tarea', this.formatOptionalUsd(budget.task_limit_usd)],
            ['Límite modo', this.formatOptionalUsd(budget.mode_limit_usd)],
            ['Límite subagente', this.formatOptionalUsd(budget.subagent_effective_limit_usd)],
            ['Límite diario', this.formatOptionalUsd(budget.daily_limit_usd)],
            ['Límite mensual', this.formatOptionalUsd(budget.monthly_limit_usd)],
            ['Auto-stop', budget.stop_required ? 'si' : 'no'],
            ['Alertas', alerts.length ? alerts[0] : 'sin alertas'],
        ];
        this.costCurrentStateEl.innerHTML = cards
            .map(([label, value]) => `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`)
            .join('');
    }

    renderCostWeeklyReport() {
        if (this.costWeeklySummaryEl) {
            const report = this.costWeeklyReport;
            if (!report) {
                this.costWeeklySummaryEl.innerHTML = '<div class="code-empty-state">Sin reporte semanal disponible todavía.</div>';
            } else {
                const totals = report.totals || {};
                const previous = report.previous_totals || {};
                const topProvider = Array.isArray(report.provider_breakdown) && report.provider_breakdown.length
                    ? report.provider_breakdown[0]
                    : null;
                const cards = [
                    ['Ventana', `${report.week_start_local || '-'} → ${report.week_end_local || '-'}`],
                    ['Semana', report.window_label || '-'],
                    ['Total', this.formatUsd(totals.total_cost_usd)],
                    ['Semana previa', this.formatUsd(previous.total_cost_usd)],
                    ['Delta', this.formatWeeklyDelta(report.delta_total_cost_usd, report.delta_percent)],
                    ['Eventos', String(Number(totals.event_count || 0))],
                    ['Tokens', this.formatTokenCount(totals.total_tokens)],
                    ['Top proveedor', topProvider ? `${topProvider.label || topProvider.key} (${this.formatUsd(topProvider.total_cost_usd)})` : 'sin datos'],
                    ['Delivery', report.delivery_status || 'preview_only'],
                    ['Targets', Array.isArray(report.delivery_targets) && report.delivery_targets.length ? report.delivery_targets.join(', ') : 'sin targets'],
                ];
                this.costWeeklySummaryEl.innerHTML = cards
                    .map(([label, value]) => `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`)
                    .join('');
            }
        }

        if (!this.costWeeklyBreakdownEl) return;
        const report = this.costWeeklyReport;
        if (!report) {
            this.costWeeklyBreakdownEl.innerHTML = '<div class="code-empty-state">Sin breakdown semanal disponible.</div>';
            return;
        }

        const providerItems = Array.isArray(report.provider_breakdown) ? report.provider_breakdown : [];
        const modeItems = Array.isArray(report.mode_breakdown) ? report.mode_breakdown : [];
        const workerItems = Array.isArray(report.worker_kind_breakdown) ? report.worker_kind_breakdown : [];
        const dayItems = Array.isArray(report.daily_breakdown) ? report.daily_breakdown : [];
        const highlightItems = Array.isArray(report.highlights) ? report.highlights : [];

        const renderBreakdownItems = (items, emptyMessage) => items.length
            ? items.map((item) => `
                <div class="code-list-item cost-event-item">
                    <span class="code-list-icon">${this.escapeHtml(item.label || item.key || '--')}</span>
                    <span class="code-list-stack">
                        <span class="code-list-text">${this.escapeHtml(this.formatUsd(item.total_cost_usd))} | ${this.escapeHtml(String(item.share_percent || 0))}%</span>
                        <span class="code-list-meta">eventos: ${this.escapeHtml(String(item.event_count || 0))} | tokens: ${this.escapeHtml(this.formatTokenCount(item.total_tokens))} | ultimo: ${this.escapeHtml(this.formatDateTime(item.last_event_at))}</span>
                    </span>
                </div>
            `).join('')
            : `<div class="code-empty-state">${this.escapeHtml(emptyMessage)}</div>`;

        const providerHtml = renderBreakdownItems(providerItems, 'Sin gasto por proveedor en esta ventana.');
        const modeHtml = renderBreakdownItems(modeItems, 'Sin gasto por modo en esta ventana.');
        const workerHtml = renderBreakdownItems(workerItems, 'Sin gasto por tipo de worker en esta ventana.');

        const dailyHtml = dayItems.length
            ? dayItems.map((item) => `
                <div class="code-list-item cost-event-item">
                    <span class="code-list-icon">${this.escapeHtml(item.label || item.date || '--')}</span>
                    <span class="code-list-stack">
                        <span class="code-list-text">${this.escapeHtml(this.formatUsd(item.total_cost_usd))}</span>
                        <span class="code-list-meta">eventos: ${this.escapeHtml(String(item.event_count || 0))} | tokens: ${this.escapeHtml(this.formatTokenCount(item.total_tokens))}</span>
                    </span>
                </div>
            `).join('')
            : '<div class="code-empty-state">Sin gasto diario en esta ventana.</div>';

        const highlightsHtml = highlightItems.length
            ? highlightItems.map((item) => `<div class="code-list-item"><span class="code-list-stack"><span class="code-list-text">${this.escapeHtml(item)}</span></span></div>`).join('')
            : '<div class="code-empty-state">Sin highlights semanales.</div>';

        this.costWeeklyBreakdownEl.innerHTML = [
            '<div class="code-subsection-title">Highlights</div>',
            highlightsHtml,
            '<div class="code-subsection-title">Top proveedores</div>',
            providerHtml,
            '<div class="code-subsection-title">Top modos</div>',
            modeHtml,
            '<div class="code-subsection-title">Top workers</div>',
            workerHtml,
            '<div class="code-subsection-title">Dias de la ventana</div>',
            dailyHtml,
        ].join('');
    }

    renderGatewaySummary() {
        if (!this.gatewaySummaryEl) return;
        const status = this.gatewayStatus;
        if (!status) {
            this.gatewaySummaryEl.innerHTML = '<div class="code-empty-state">Gateway sin estado disponible todavia.</div>';
            return;
        }
        const localApp = Array.isArray(status.channels)
            ? status.channels.find((item) => item.channel === 'local_app')
            : null;
        const cards = [
            ['Gateway', status.enabled ? 'activo' : 'desactivado'],
            ['Router', status.session_router_enabled ? 'activo' : 'off'],
            ['Canal default', status.default_channel || 'local_app'],
            ['Sesion default', status.default_session_key || 'main'],
            ['Sesiones', String(Number(status.connected_sessions || 0))],
            ['En cola', String(Number(status.queued_notifications || 0))],
            ['Entregadas', String(Number(status.delivered_notifications || 0))],
            ['Fallidas', String(Number(status.failed_notifications || 0))],
            ['Local app', localApp ? (localApp.ready ? 'ready' : (localApp.detail || 'sin detalle')) : 'sin canal'],
            ['Ultimo check', this.formatDateTime(status.checked_at)],
        ];
        this.gatewaySummaryEl.innerHTML = cards
            .map(([label, value]) => `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`)
            .join('');
    }

    renderGatewayOutbox() {
        if (!this.gatewayOutboxEl) return;
        const items = Array.isArray(this.gatewayOutbox) ? this.gatewayOutbox : [];
        if (items.length === 0) {
            this.gatewayOutboxEl.innerHTML = '<div class="code-empty-state">Sin notificaciones en outbox.</div>';
            return;
        }
        this.gatewayOutboxEl.innerHTML = items.map((item) => `
            <div class="code-list-item gateway-outbox-item ${this.getGatewayStatusClass(item.status)}">
                <span class="code-list-icon">${this.escapeHtml(item.level || '--')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(item.title || item.notification_id || '--')}</span>
                    <span class="code-list-meta">${this.escapeHtml(item.channel || 'local_app')}:${this.escapeHtml(item.session_key || 'main')} | ${this.escapeHtml(item.status || 'queued')} | source: ${this.escapeHtml(item.source_type || '-')} | created: ${this.escapeHtml(this.formatDateTime(item.created_at))}${item.delivered_at ? ` | delivered: ${this.escapeHtml(this.formatDateTime(item.delivered_at))}` : ''}${item.last_error ? ` | error: ${this.escapeHtml(item.last_error)}` : ''}</span>
                    ${item.body ? `<span class="code-list-meta">${this.escapeHtml(item.body)}</span>` : ''}
                </span>
            </div>
        `).join('');
    }

    renderSchedulerJobs() {
        const jobs = Array.isArray(this.schedulerJobs) ? this.schedulerJobs : [];
        if (jobs.length === 0) {
            this.schedulerJobListEl.innerHTML = '<div class="code-empty-state">No hay jobs programados.</div>';
            return;
        }
        this.schedulerJobListEl.innerHTML = jobs.map((job) => `
            <button class="code-list-item scheduler-job-item ${job.job_id === this.selectedJobId ? 'is-selected' : ''}" data-job-id="${this.escapeHtml(job.job_id)}">
                <span class="code-list-icon">${this.escapeHtml(job.trigger_type || '--')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(job.name || job.job_id)}</span>
                    <span class="code-list-meta">${this.escapeHtml(this.formatSchedule(job))} | next: ${this.escapeHtml(this.formatDateTime(job.next_run_at))} | signal: ${this.escapeHtml(this.formatDateTime(job.last_signal_at))} | ${job.enabled ? 'enabled' : 'paused'} | retries: ${this.escapeHtml(this.formatRetryPolicy(job))}${Number(job.retry_attempt || 0) > 0 ? ` | retry activo #${this.escapeHtml(String(job.retry_attempt || 0))}` : ''}${job.last_error ? ' | error' : ''}</span>
                </span>
            </button>
        `).join('');
    }

    renderSchedulerRuns() {
        const runs = Array.isArray(this.schedulerRuns) ? this.schedulerRuns : [];
        if (runs.length === 0) {
            this.schedulerRunsEl.innerHTML = '<div class="code-empty-state">Sin ejecuciones registradas todavia.</div>';
            return;
        }
        this.schedulerRunsEl.innerHTML = runs.map((run) => `
            <div class="code-list-item scheduler-run-item ${this.getSchedulerStatusClass(run.status)}">
                <span class="code-list-icon">${this.escapeHtml(run.status || '--')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(this.formatDateTime(run.started_at))}</span>
                    <span class="code-list-meta">trigger: ${this.escapeHtml(run.trigger_source || '-')} | duracion: ${this.escapeHtml(String(run.duration_ms || 0))} ms${this.formatRunRetryMeta(run)}${run.error ? ` | error: ${this.escapeHtml(run.error)}` : ''}</span>
                </span>
            </div>
        `).join('');
    }

    renderSchedulerCheckpoints() {
        if (!this.schedulerCheckpointsEl) return;
        const checkpoints = Array.isArray(this.schedulerCheckpoints) ? this.schedulerCheckpoints : [];
        if (checkpoints.length === 0) {
            this.schedulerCheckpointsEl.innerHTML = '<div class="code-empty-state">Sin checkpoints registrados para esta vista.</div>';
            return;
        }
        this.schedulerCheckpointsEl.innerHTML = checkpoints.map((checkpoint) => `
            <div class="code-list-item scheduler-checkpoint-item ${this.getSchedulerStatusClass(checkpoint.status)}">
                <span class="code-list-icon">${this.escapeHtml(checkpoint.checkpoint_type || '--')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(this.formatDateTime(checkpoint.created_at))} | ${this.escapeHtml(checkpoint.status || '-')} | ${this.escapeHtml(String(Math.round(Number(checkpoint.progress || 0))))}%</span>
                    <span class="code-list-meta">${this.escapeHtml(checkpoint.message || '-')} | run: ${this.escapeHtml(checkpoint.run_id || '-')} ${this.formatCheckpointPayload(checkpoint.payload)}</span>
                </span>
            </div>
        `).join('');
    }

    renderCostEvents() {
        if (!this.costEventsEl) return;
        const events = Array.isArray(this.costEvents) ? this.costEvents : [];
        if (events.length === 0) {
            this.costEventsEl.innerHTML = '<div class="code-empty-state">Sin eventos de costo registrados todavía.</div>';
            return;
        }
        this.costEventsEl.innerHTML = events.map((event) => `
            <div class="code-list-item cost-event-item">
                <span class="code-list-icon">${this.escapeHtml(event.provider || '--')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(this.formatDateTime(event.created_at))} | ${this.escapeHtml(event.model || '-')} | ${this.escapeHtml(this.formatUsd(event.total_cost_usd))}</span>
                    <span class="code-list-meta">source: ${this.escapeHtml(event.source || '-')} | mode: ${this.escapeHtml(event.mode_key || '-')} | worker: ${this.escapeHtml(event.worker_kind || '-')} ${event.worker_id ? `(${this.escapeHtml(event.worker_id)})` : ''} | tokens: ${this.escapeHtml(this.formatTokenCount(event.total_tokens))}${event.estimated ? ' | estimado' : ''}${event.pricing_missing ? ' | sin precio configurado' : ''}</span>
                </span>
            </div>
        `).join('');
    }

    async handleSchedulerJobClick(event) {
        const button = event.target.closest('[data-job-id]');
        if (!button) return;
        this.selectedJobId = button.dataset.jobId || '';
        this.populateSchedulerForm(this.getSchedulerJob(this.selectedJobId));
        this.renderSchedulerJobs();
        await Promise.all([
            this.loadSchedulerRuns(this.selectedJobId),
            this.loadSchedulerCheckpoints(this.selectedJobId),
        ]);
        this.setSchedulerStatus(`Job cargado: ${this.selectedJobId}`);
    }

    getSchedulerJob(jobId) {
        return this.schedulerJobs.find((job) => job.job_id === jobId) || null;
    }

    populateSchedulerForm(job) {
        if (!job) return;
        this.schedulerNameInput.value = job.name || '';
        this.schedulerTaskTypeSelect.value = job.task_type || 'skill';
        this.schedulerTriggerTypeSelect.value = job.trigger_type || 'interval';
        this.schedulerEnabledCheckbox.checked = !!job.enabled;
        this.schedulerIntervalInput.value = job.interval_seconds != null ? String(job.interval_seconds) : '';
        this.schedulerCronInput.value = job.cron_expression || '';
        this.schedulerEventNameInput.value = job.event_name || '';
        this.schedulerWebhookPathInput.value = job.webhook_path || '';
        this.schedulerWebhookSecretInput.value = job.webhook_secret || '';
        this.schedulerHeartbeatKeyInput.value = job.heartbeat_key || 'system';
        this.schedulerHeartbeatIntervalSecondsInput.value = job.heartbeat_interval_seconds != null ? String(job.heartbeat_interval_seconds) : '';
        this.schedulerMaxRetriesInput.value = String(job.max_retries ?? 0);
        this.schedulerRetryBackoffSecondsInput.value = String(job.retry_backoff_seconds ?? 30);
        this.schedulerRetryBackoffMultiplierInput.value = String(job.retry_backoff_multiplier ?? 2.0);
        this.schedulerPayloadInput.value = JSON.stringify(job.payload || {}, null, 2);
        this.setSchedulerFormMeta(`Editando ${job.job_id}. Para cambiar task_type o trigger_type, crea un job nuevo. Retry actual: ${Number(job.retry_attempt || 0)}. Ultima señal: ${this.formatDateTime(job.last_signal_at)}.`);
        this.updateSchedulerFormLockState();
        this.syncSchedulerTriggerInputs();
    }

    clearSchedulerForm(isNewDraft = false) {
        this.selectedJobId = '';
        this.schedulerNameInput.value = '';
        this.schedulerTaskTypeSelect.value = 'skill';
        this.schedulerTriggerTypeSelect.value = 'interval';
        this.schedulerEnabledCheckbox.checked = true;
        this.schedulerIntervalInput.value = '60';
        this.schedulerCronInput.value = '';
        this.schedulerEventNameInput.value = '';
        this.schedulerWebhookPathInput.value = '';
        this.schedulerWebhookSecretInput.value = '';
        this.schedulerHeartbeatKeyInput.value = 'system';
        this.schedulerHeartbeatIntervalSecondsInput.value = '';
        this.schedulerMaxRetriesInput.value = '0';
        this.schedulerRetryBackoffSecondsInput.value = '30';
        this.schedulerRetryBackoffMultiplierInput.value = '2.0';
        this.schedulerPayloadInput.value = JSON.stringify({ skill_id: 'file-manager', tool: 'inspect_text_file', input: { path: 'C:\\ruta\\archivo.txt' } }, null, 2);
        this.schedulerRuns = [];
        this.schedulerCheckpoints = [];
        this.renderSchedulerRuns();
        this.renderSchedulerCheckpoints();
        this.renderSchedulerJobs();
        this.renderSchedulerCurrentState();
        this.updateSchedulerFormLockState();
        this.syncSchedulerTriggerInputs();
        this.setSchedulerFormMeta(isNewDraft ? 'Nuevo job. Define nombre, trigger y payload JSON.' : '');
    }

    updateSchedulerFormLockState() {
        const editingExisting = Boolean(this.selectedJobId);
        const triggerType = this.getSelectedTriggerType();
        this.schedulerTaskTypeSelect.disabled = editingExisting;
        this.schedulerTriggerTypeSelect.disabled = editingExisting;
        this.btnRunSchedulerJob.disabled = !editingExisting;
        this.btnTriggerSchedulerJob.disabled = !editingExisting || !this.isSignalTriggerType(triggerType);
        this.btnDeleteSchedulerJob.disabled = !editingExisting;
        this.btnSaveSchedulerJob.textContent = editingExisting ? 'Actualizar job' : 'Crear job';
    }

    syncSchedulerTriggerInputs() {
        const triggerType = this.getSelectedTriggerType();
        const isInterval = triggerType === 'interval';
        const isCron = triggerType === 'cron';
        const isEvent = triggerType === 'event';
        const isWebhook = triggerType === 'webhook';
        const isHeartbeat = triggerType === 'heartbeat';
        this.schedulerIntervalInput.disabled = !isInterval;
        this.schedulerCronInput.disabled = !isCron;
        this.schedulerEventNameInput.disabled = !isEvent;
        this.schedulerWebhookPathInput.disabled = !isWebhook;
        this.schedulerWebhookSecretInput.disabled = !isWebhook;
        this.schedulerHeartbeatKeyInput.disabled = !isHeartbeat;
        this.schedulerHeartbeatIntervalSecondsInput.disabled = !isHeartbeat;
        if (isInterval && !this.schedulerIntervalInput.value) this.schedulerIntervalInput.value = '60';
        if (isHeartbeat && !this.schedulerHeartbeatKeyInput.value) this.schedulerHeartbeatKeyInput.value = 'system';
        this.updateSchedulerFormLockState();
    }

    collectSchedulerFormData() {
        const name = String(this.schedulerNameInput.value || '').trim();
        if (!name) throw new Error('El nombre del job es obligatorio.');
        let payload;
        try {
            payload = JSON.parse(this.schedulerPayloadInput.value || '{}');
        } catch (error) {
            throw new Error(`Payload JSON invalido: ${error.message || error}`);
        }
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('El payload debe ser un objeto JSON.');
        const taskType = String(this.schedulerTaskTypeSelect.value || '').trim();
        const triggerType = String(this.schedulerTriggerTypeSelect.value || '').trim();
        const data = { name, payload, enabled: !!this.schedulerEnabledCheckbox.checked };
        if (!this.selectedJobId) {
            data.task_type = taskType;
            data.trigger_type = triggerType;
        }
        if (triggerType === 'interval') {
            const intervalSeconds = Number(this.schedulerIntervalInput.value || 0);
            if (!Number.isFinite(intervalSeconds) || intervalSeconds < 5) throw new Error('El intervalo debe ser mayor o igual a 5 segundos.');
            data.interval_seconds = Math.trunc(intervalSeconds);
            data.cron_expression = null;
            data.event_name = null;
            data.webhook_path = null;
            data.webhook_secret = null;
            data.heartbeat_key = null;
            data.heartbeat_interval_seconds = null;
        } else if (triggerType === 'cron') {
            const cronExpression = String(this.schedulerCronInput.value || '').trim();
            if (!cronExpression) throw new Error('La expresion cron es obligatoria.');
            data.interval_seconds = null;
            data.cron_expression = cronExpression;
            data.event_name = null;
            data.webhook_path = null;
            data.webhook_secret = null;
            data.heartbeat_key = null;
            data.heartbeat_interval_seconds = null;
        } else if (triggerType === 'event') {
            const eventName = String(this.schedulerEventNameInput.value || '').trim().toLowerCase();
            if (!eventName) throw new Error('El nombre del evento es obligatorio.');
            data.interval_seconds = null;
            data.cron_expression = null;
            data.event_name = eventName;
            data.webhook_path = null;
            data.webhook_secret = null;
            data.heartbeat_key = null;
            data.heartbeat_interval_seconds = null;
        } else if (triggerType === 'webhook') {
            const webhookPath = this.normalizeWebhookPath(String(this.schedulerWebhookPathInput.value || ''));
            if (!webhookPath) throw new Error('El webhook path es obligatorio.');
            data.interval_seconds = null;
            data.cron_expression = null;
            data.event_name = null;
            data.webhook_path = webhookPath;
            data.webhook_secret = String(this.schedulerWebhookSecretInput.value || '').trim() || null;
            data.heartbeat_key = null;
            data.heartbeat_interval_seconds = null;
        } else if (triggerType === 'heartbeat') {
            const heartbeatKey = String(this.schedulerHeartbeatKeyInput.value || '').trim().toLowerCase() || 'system';
            const rawHeartbeatInterval = String(this.schedulerHeartbeatIntervalSecondsInput.value || '').trim();
            let heartbeatIntervalSeconds = null;
            if (rawHeartbeatInterval) {
                const parsed = Number(rawHeartbeatInterval);
                if (!Number.isFinite(parsed) || parsed < 1) throw new Error('El intervalo minimo de heartbeat debe ser >= 1 segundo.');
                heartbeatIntervalSeconds = Math.trunc(parsed);
            }
            data.interval_seconds = null;
            data.cron_expression = null;
            data.event_name = null;
            data.webhook_path = null;
            data.webhook_secret = null;
            data.heartbeat_key = heartbeatKey;
            data.heartbeat_interval_seconds = heartbeatIntervalSeconds;
        } else {
            throw new Error(`Trigger type no soportado: ${triggerType}`);
        }
        const maxRetries = Number(this.schedulerMaxRetriesInput.value || 0);
        if (!Number.isFinite(maxRetries) || maxRetries < 0) throw new Error('Max retries debe ser un entero >= 0.');
        const retryBackoffSeconds = Number(this.schedulerRetryBackoffSecondsInput.value || 0);
        if (!Number.isFinite(retryBackoffSeconds) || retryBackoffSeconds < 1) throw new Error('El backoff base debe ser >= 1 segundo.');
        const retryBackoffMultiplier = Number(this.schedulerRetryBackoffMultiplierInput.value || 0);
        if (!Number.isFinite(retryBackoffMultiplier) || retryBackoffMultiplier < 1) throw new Error('El multiplicador de backoff debe ser >= 1.0.');
        data.max_retries = Math.trunc(maxRetries);
        data.retry_backoff_seconds = Math.trunc(retryBackoffSeconds);
        data.retry_backoff_multiplier = Number(retryBackoffMultiplier.toFixed(2));
        return data;
    }

    async saveSchedulerJob() {
        try {
            const body = this.collectSchedulerFormData();
            this.setSchedulerStatus(this.selectedJobId ? 'Actualizando job...' : 'Creando job...');
            const data = this.selectedJobId
                ? await this.requestJson(`/scheduler/jobs/${encodeURIComponent(this.selectedJobId)}`, {
                    method: 'PUT',
                    body: {
                        name: body.name,
                        payload: body.payload,
                        interval_seconds: body.interval_seconds,
                        cron_expression: body.cron_expression,
                        event_name: body.event_name,
                        webhook_path: body.webhook_path,
                        webhook_secret: body.webhook_secret,
                        heartbeat_key: body.heartbeat_key,
                        heartbeat_interval_seconds: body.heartbeat_interval_seconds,
                        max_retries: body.max_retries,
                        retry_backoff_seconds: body.retry_backoff_seconds,
                        retry_backoff_multiplier: body.retry_backoff_multiplier,
                        enabled: body.enabled,
                    },
                })
                : await this.requestJson('/scheduler/jobs', { method: 'POST', body });
            if (!data.success) throw new Error(data.error || 'No se pudo guardar el job.');
            const nextJobId = data.job?.job_id || this.selectedJobId || '';
            await this.loadScheduler({ preserveSelection: false });
            if (nextJobId) {
                this.selectedJobId = nextJobId;
                this.populateSchedulerForm(this.getSchedulerJob(nextJobId));
                this.renderSchedulerJobs();
                await this.loadSchedulerRuns(nextJobId);
            }
            this.setSchedulerStatus(nextJobId ? `Job listo: ${nextJobId}` : 'Job guardado.');
        } catch (error) {
            this.setSchedulerStatus(error.message || 'No se pudo guardar el job.', true);
        }
    }

    async runSelectedSchedulerJob() {
        if (!this.selectedJobId) {
            this.setSchedulerStatus('Selecciona un job antes de ejecutarlo.', true);
            return;
        }
        try {
            this.setSchedulerStatus(`Ejecutando ${this.selectedJobId}...`);
            const data = await this.requestJson(`/scheduler/jobs/${encodeURIComponent(this.selectedJobId)}/run`, { method: 'POST' });
            if (!data.success) throw new Error(data.error || 'No se pudo ejecutar el job.');
            await this.loadSchedulerRuns(this.selectedJobId);
            await this.loadScheduler({ preserveSelection: true });
            const runResult = data.run?.result || {};
            if (data.run?.status && data.run.status !== 'success' && !runResult.retry_scheduled) {
                this.setSchedulerStatus(`Job fallo sin retry: ${this.selectedJobId}`, true);
            } else if (runResult.retry_scheduled) {
                this.setSchedulerStatus(`Job fallo y dejo retry #${runResult.retry_attempt || 0} en ${runResult.retry_delay_seconds || 0}s: ${this.selectedJobId}`);
            } else {
                this.setSchedulerStatus(`Job ejecutado: ${this.selectedJobId}`);
            }
        } catch (error) {
            this.setSchedulerStatus(error.message || 'No se pudo ejecutar el job.', true);
        }
    }

    async triggerSelectedSchedulerJob() {
        if (!this.selectedJobId) {
            this.setSchedulerStatus('Selecciona un job antes de disparar su trigger.', true);
            return;
        }
        const job = this.getSchedulerJob(this.selectedJobId);
        if (!job) {
            this.setSchedulerStatus('No se encontro el job seleccionado.', true);
            return;
        }
        const triggerType = String(job.trigger_type || '').trim();
        if (!this.isSignalTriggerType(triggerType)) {
            this.setSchedulerStatus('Solo los jobs event, webhook o heartbeat admiten disparo manual de trigger.', true);
            return;
        }
        try {
            this.setSchedulerStatus(`Disparando trigger ${triggerType} para ${this.selectedJobId}...`);
            const request = this.buildTriggerRequest(job);
            const data = await this.requestJson(request.path, { method: 'POST', body: request.body });
            if (!data.success) throw new Error(data.error || 'No se pudo disparar el trigger.');
            await this.loadScheduler({ preserveSelection: true });
            await this.loadSchedulerRuns(this.selectedJobId);
            this.setSchedulerStatus(
                `Trigger ${triggerType} disparado: ejecutados ${data.executed_jobs || 0}, en cola ${data.queued_jobs || 0}, omitidos ${data.skipped_jobs || 0}.`
            );
        } catch (error) {
            this.setSchedulerStatus(error.message || 'No se pudo disparar el trigger.', true);
        }
    }

    async deleteSelectedSchedulerJob() {
        if (!this.selectedJobId) {
            this.setSchedulerStatus('Selecciona un job antes de eliminarlo.', true);
            return;
        }
        const job = this.getSchedulerJob(this.selectedJobId);
        const label = job?.name || this.selectedJobId;
        if (!window.confirm(`Eliminar el job programado "${label}"?`)) return;
        try {
            this.setSchedulerStatus(`Eliminando ${this.selectedJobId}...`);
            const data = await this.requestJson(`/scheduler/jobs/${encodeURIComponent(this.selectedJobId)}`, { method: 'DELETE' });
            if (!data.success) throw new Error(data.error || 'No se pudo eliminar el job.');
            this.clearSchedulerForm(false);
            await this.loadScheduler({ preserveSelection: false });
            this.setSchedulerStatus(`Job eliminado: ${label}`);
        } catch (error) {
            this.setSchedulerStatus(error.message || 'No se pudo eliminar el job.', true);
        }
    }

    formatSchedule(job) {
        if (!job) return '-';
        if (job.trigger_type === 'cron') return `cron ${job.cron_expression || '-'}`;
        if (job.trigger_type === 'interval') return `cada ${job.interval_seconds || 0}s`;
        if (job.trigger_type === 'event') return `evento ${job.event_name || '-'}`;
        if (job.trigger_type === 'webhook') return `webhook /${job.webhook_path || '-'}`;
        if (job.trigger_type === 'heartbeat') {
            const throttle = job.heartbeat_interval_seconds ? ` / min ${job.heartbeat_interval_seconds}s` : '';
            return `heartbeat ${job.heartbeat_key || 'system'}${throttle}`;
        }
        return job.trigger_type || '-';
    }

    formatRetryPolicy(job) {
        if (!job) return '-';
        return `${job.max_retries || 0} x ${job.retry_backoff_seconds || 30}s * ${job.retry_backoff_multiplier || 2.0}`;
    }

    formatOptionalUsd(value) {
        const numeric = Number(value || 0);
        if (!Number.isFinite(numeric) || numeric <= 0) return 'sin limite';
        return this.formatUsd(numeric);
    }

    formatBudgetBucket(bucket) {
        if (!bucket || typeof bucket !== 'object') return '-';
        const state = String(bucket.state || 'ok');
        if (state === 'unlimited') return 'sin limite';
        const usagePercent = Number(bucket.usage_percent || 0);
        const remaining = bucket.remaining_usd === null || bucket.remaining_usd === undefined
            ? 'n/a'
            : this.formatUsd(bucket.remaining_usd);
        return `${state} | ${usagePercent}% | restante ${remaining}`;
    }

    formatUsd(value) {
        const numeric = Number(value || 0);
        if (!Number.isFinite(numeric)) return '$0.0000';
        return `$${numeric.toFixed(4)}`;
    }

    formatTokenCount(value) {
        const numeric = Number(value || 0);
        if (!Number.isFinite(numeric)) return '0';
        return numeric.toLocaleString('es-CO');
    }

    formatRunRetryMeta(run) {
        const result = run?.result;
        if (!result || typeof result !== 'object') return '';
        if (result.retry_scheduled) {
            return ` | retry #${this.escapeHtml(String(result.retry_attempt || 0))} en ${this.escapeHtml(String(result.retry_delay_seconds || 0))}s`;
        }
        return '';
    }

    formatWeeklyDelta(deltaUsd, deltaPercent) {
        const numericDelta = Number(deltaUsd || 0);
        const direction = numericDelta > 0 ? '+' : numericDelta < 0 ? '-' : '±';
        const percentText = Number.isFinite(Number(deltaPercent))
            ? ` (${Number(deltaPercent).toFixed(2)}%)`
            : '';
        return `${direction}${this.formatUsd(Math.abs(numericDelta))}${percentText}`;
    }

    formatCheckpointPayload(payload) {
        if (!payload || typeof payload !== 'object') return '';
        const parts = [];
        if (payload.next_run_at) parts.push(`next: ${this.formatDateTime(payload.next_run_at)}`);
        if (payload.retry_scheduled) parts.push(`retry #${payload.retry_attempt || 0} en ${payload.retry_delay_seconds || 0}s`);
        if (payload.trigger_source) parts.push(`source: ${payload.trigger_source}`);
        return parts.length ? `| ${this.escapeHtml(parts.join(' | '))}` : '';
    }

    getSchedulerStatusClass(status) {
        const normalized = String(status || '').trim().toLowerCase();
        if (normalized === 'success') return 'is-success';
        if (normalized === 'running') return 'is-running';
        if (normalized === 'interrupted') return 'is-warning';
        return 'is-error';
    }

    getGatewayStatusClass(status) {
        const normalized = String(status || '').trim().toLowerCase();
        if (normalized === 'delivered') return 'is-success';
        if (normalized === 'queued') return 'is-warning';
        if (normalized === 'failed') return 'is-error';
        return '';
    }

    formatDateTime(value) {
        if (!value) return '-';
        try {
            return new Date(value).toLocaleString('es-CO');
        } catch (error) {
            return String(value);
        }
    }

    getSelectedTriggerType() {
        return String(this.schedulerTriggerTypeSelect?.value || 'interval').trim();
    }

    isSignalTriggerType(triggerType) {
        return ['heartbeat', 'event', 'webhook'].includes(String(triggerType || '').trim());
    }

    normalizeWebhookPath(path) {
        return String(path || '').trim().replace(/\\/g, '/').replace(/^\/+|\/+$/g, '').toLowerCase();
    }

    buildTriggerRequest(job) {
        if (!job) throw new Error('No hay job cargado para disparar el trigger.');
        const triggerType = String(job.trigger_type || '').trim();
        if (triggerType === 'event') {
            if (!job.event_name) throw new Error('El job no tiene event_name.');
            return {
                path: `/scheduler/events/${encodeURIComponent(job.event_name)}/emit`,
                body: { payload: {} },
            };
        }
        if (triggerType === 'heartbeat') {
            return {
                path: `/scheduler/heartbeat/${encodeURIComponent(job.heartbeat_key || 'system')}/emit`,
                body: { payload: {} },
            };
        }
        if (triggerType === 'webhook') {
            const normalizedPath = this.normalizeWebhookPath(job.webhook_path || '');
            if (!normalizedPath) throw new Error('El job no tiene webhook_path.');
            return {
                path: `/scheduler/webhooks/${normalizedPath}`,
                body: {
                    payload: {},
                    secret: job.webhook_secret || null,
                },
            };
        }
        throw new Error(`Trigger type no soportado para disparo manual: ${triggerType}`);
    }

    getParentPath(path) {
        const normalized = String(path || '').replace(/[\\/]+$/, '');
        const parts = normalized.split(/[\\/]/);
        parts.pop();
        return parts.join('\\') || normalized;
    }

    resolveWorkspacePath(path) {
        if (!path) return this.currentWorkspaceRoot;
        if (/^[a-zA-Z]:\\/.test(path) || path.startsWith('\\\\')) return path;
        const root = String(this.currentWorkspaceRoot || '').replace(/[\\/]+$/, '');
        return `${root}\\${String(path).replace(/^[/\\]+/, '')}`;
    }

    setWorkspaceStatus(message, isError = false) {
        if (!this.statusEl) return;
        this.statusEl.textContent = String(message || '');
        this.statusEl.classList.toggle('code-status-error', Boolean(isError));
    }

    setSchedulerStatus(message, isError = false) {
        if (!this.schedulerStatusEl) return;
        this.schedulerStatusEl.textContent = String(message || '');
        this.schedulerStatusEl.classList.toggle('code-status-error', Boolean(isError));
    }

    setSchedulerFormMeta(message, isError = false) {
        if (!this.schedulerFormMetaEl) return;
        this.schedulerFormMetaEl.textContent = String(message || '');
        this.schedulerFormMetaEl.classList.toggle('code-status-error', Boolean(isError));
    }

    async fetchJson(path, params) {
        return this.requestJson(path, { query: params });
    }

    async requestJson(path, options = {}) {
        const query = new URLSearchParams();
        Object.entries(options.query || {}).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') return;
            query.set(key, String(value));
        });
        const url = `${this.apiBase}${path}${query.toString() ? `?${query.toString()}` : ''}`;
        const requestOptions = { method: options.method || 'GET', headers: {} };
        if (options.body !== undefined) {
            requestOptions.headers['Content-Type'] = 'application/json';
            requestOptions.body = JSON.stringify(options.body);
        }
        const response = await fetch(url, requestOptions);
        let data = null;
        try { data = await response.json(); } catch (error) { data = null; }
        if (!response.ok) throw new Error(data?.detail || data?.error || response.statusText || `HTTP ${response.status}`);
        return data;
    }

    // ── Canvas Tab ──────────────────────────────────────────────────────

    async loadCanvas() {
        this.setCanvasStatus('Cargando canvases...');
        try {
            const canvasType = this.selectCanvasType?.value || '';
            const data = await this.fetchJson('/canvas', { canvas_type: canvasType || undefined });
            this.canvasLoaded = true;
            this.canvases = Array.isArray(data.canvases) ? data.canvases : [];
            this.renderCanvasList();
            if (this.selectedCanvasId && this.canvases.some((c) => c.canvas_id === this.selectedCanvasId)) {
                await this.loadCanvasContent(this.selectedCanvasId);
            } else {
                this.selectedCanvasId = '';
                this.renderCanvasViewer(null);
                this.canvasVersions = [];
                this.renderCanvasVersions();
            }
            this.setCanvasStatus(`${this.canvases.length} canvas(es) encontrado(s).`);
        } catch (error) {
            this.canvases = [];
            this.renderCanvasList();
            this.renderCanvasViewer(null);
            this.setCanvasStatus(error.message || 'No se pudieron cargar los canvases.', true);
        }
    }

    renderCanvasList() {
        if (!this.canvasListEl) return;
        const items = Array.isArray(this.canvases) ? this.canvases : [];
        if (items.length === 0) {
            this.canvasListEl.innerHTML = '<div class="code-empty-state">No hay canvases creados.</div>';
            return;
        }
        this.canvasListEl.innerHTML = items.map((c) => `
            <button class="code-list-item ${c.canvas_id === this.selectedCanvasId ? 'is-selected' : ''}" data-canvas-id="${this.escapeHtml(c.canvas_id)}">
                <span class="code-list-icon">${this.escapeHtml(c.canvas_type || 'custom')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(c.title || c.canvas_id)}${c.pinned ? ' ' + CODE_ICONS.pin : ''}</span>
                    <span class="code-list-meta">${this.escapeHtml(this.formatDateTime(c.updated_at || c.created_at))}</span>
                </span>
            </button>
        `).join('');
    }

    async handleCanvasListClick(event) {
        const btn = event.target.closest('[data-canvas-id]');
        if (!btn) return;
        this.selectedCanvasId = btn.dataset.canvasId || '';
        this.renderCanvasList();
        await this.loadCanvasContent(this.selectedCanvasId);
        await this.loadCanvasVersions();
    }

    async loadCanvasContent(canvasId) {
        if (!canvasId) { this.renderCanvasViewer(null); return; }
        try {
            const data = await this.fetchJson(`/canvas/${encodeURIComponent(canvasId)}`);
            this.renderCanvasViewer(data.canvas || null);
        } catch (error) {
            this.renderCanvasViewer(null);
            this.setCanvasStatus(error.message || 'No se pudo cargar el canvas.', true);
        }
    }

    renderCanvasViewer(canvas) {
        if (this.canvasCurrentTitle) {
            this.canvasCurrentTitle.textContent = canvas ? (canvas.title || canvas.canvas_id) : 'Selecciona un canvas';
        }
        if (this.canvasRenderArea) {
            if (canvas && canvas.html_content) {
                this.canvasRenderArea.innerHTML = canvas.html_content;
            } else if (canvas && canvas.content) {
                this.canvasRenderArea.innerHTML = `<pre style="white-space:pre-wrap;color:var(--text-secondary);font-size:12px;">${this.escapeHtml(typeof canvas.content === 'string' ? canvas.content : JSON.stringify(canvas.content, null, 2))}</pre>`;
            } else {
                this.canvasRenderArea.innerHTML = '<div class="canvas-placeholder">Selecciona un canvas de la lista.</div>';
            }
        }
    }

    async loadCanvasVersions() {
        if (!this.selectedCanvasId) { this.canvasVersions = []; this.renderCanvasVersions(); return; }
        try {
            const data = await this.fetchJson(`/canvas/${encodeURIComponent(this.selectedCanvasId)}/versions`, { limit: 20 });
            this.canvasVersions = Array.isArray(data.versions) ? data.versions : [];
            this.renderCanvasVersions();
        } catch (error) {
            this.canvasVersions = [];
            this.renderCanvasVersions();
        }
    }

    renderCanvasVersions() {
        if (!this.canvasVersionsListEl) return;
        const items = this.canvasVersions;
        if (items.length === 0) {
            this.canvasVersionsListEl.innerHTML = '<div class="code-empty-state">Sin versiones.</div>';
            return;
        }
        this.canvasVersionsListEl.innerHTML = items.map((v) => `
            <div class="code-list-item">
                <span class="code-list-icon">v${this.escapeHtml(String(v.version || '-'))}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(this.formatDateTime(v.created_at))}</span>
                </span>
            </div>
        `).join('');
    }

    async createCanvas() {
        const title = window.prompt('Titulo del nuevo canvas:');
        if (!title) return;
        const canvasType = this.selectCanvasType?.value || 'custom';
        try {
            this.setCanvasStatus('Creando canvas...');
            const data = await this.requestJson('/canvas', {
                method: 'POST',
                body: { title, canvas_type: canvasType, content: '' },
            });
            if (!data.ok) throw new Error(data.error || 'No se pudo crear el canvas.');
            this.selectedCanvasId = data.canvas?.canvas_id || '';
            await this.loadCanvas();
            this.setCanvasStatus(`Canvas creado: ${title}`);
        } catch (error) {
            this.setCanvasStatus(error.message || 'No se pudo crear el canvas.', true);
        }
    }

    async togglePinCanvas() {
        if (!this.selectedCanvasId) return;
        const canvas = this.canvases.find((c) => c.canvas_id === this.selectedCanvasId);
        const newPinned = !(canvas?.pinned);
        try {
            await this.requestJson(`/canvas/${encodeURIComponent(this.selectedCanvasId)}/pin`, {
                method: 'PUT', body: { pinned: newPinned },
            });
            await this.loadCanvas();
        } catch (error) {
            this.setCanvasStatus(error.message || 'No se pudo cambiar el pin.', true);
        }
    }

    async deleteSelectedCanvas() {
        if (!this.selectedCanvasId) return;
        const canvas = this.canvases.find((c) => c.canvas_id === this.selectedCanvasId);
        if (!window.confirm(`Eliminar canvas "${canvas?.title || this.selectedCanvasId}"?`)) return;
        try {
            this.setCanvasStatus('Eliminando canvas...');
            await this.requestJson(`/canvas/${encodeURIComponent(this.selectedCanvasId)}`, { method: 'DELETE' });
            this.selectedCanvasId = '';
            await this.loadCanvas();
            this.setCanvasStatus('Canvas eliminado.');
        } catch (error) {
            this.setCanvasStatus(error.message || 'No se pudo eliminar el canvas.', true);
        }
    }

    setCanvasStatus(message, isError = false) {
        if (!this.canvasStatusEl) return;
        this.canvasStatusEl.textContent = String(message || '');
        this.canvasStatusEl.classList.toggle('code-status-error', Boolean(isError));
    }

    // ── Security Tab ─────────────────────────────────────────────────────

    async loadSecurity() {
        try {
            const [usersData, policiesData, ethicalData, auditStatsData, auditRecentData, sandboxData, rateLimitsData] = await Promise.all([
                this.fetchJson('/security/rbac/users').catch(() => ({ users: [] })),
                this.fetchJson('/security/rbac/policies').catch(() => ({ policies: [] })),
                this.fetchJson('/security/ethical/restrictions').catch(() => ({ restrictions: [] })),
                this.fetchJson('/security/audit/stats').catch(() => null),
                this.fetchJson('/security/audit', { limit: 20 }).catch(() => ({ entries: [] })),
                this.fetchJson('/security/sandbox/status').catch(() => null),
                this.fetchJson('/security/rate-limits/status').catch(() => null),
            ]);
            this.securityLoaded = true;
            this.rbacUsers = Array.isArray(usersData.users) ? usersData.users : [];
            this.rbacPolicies = Array.isArray(policiesData.policies) ? policiesData.policies : [];
            this.ethicalRestrictions = Array.isArray(ethicalData.restrictions) ? ethicalData.restrictions : [];
            this.auditStats = auditStatsData;
            this.auditRecent = Array.isArray(auditRecentData.entries) ? auditRecentData.entries : [];
            this.sandboxStatus = sandboxData;
            this.rateLimits = rateLimitsData;
            this.renderRbacUsers();
            this.renderRbacPolicies();
            this.renderEthicalRestrictions();
            this.renderAuditStats();
            this.renderAuditRecent();
            this.renderSandboxStatus();
            this.renderRateLimits();
        } catch (error) {
            // silently handle
        }
    }

    renderRbacUsers() {
        if (!this.rbacUsersListEl) return;
        const users = this.rbacUsers;
        if (users.length === 0) {
            this.rbacUsersListEl.innerHTML = '<div class="code-empty-state">Sin usuarios RBAC configurados.</div>';
            return;
        }
        this.rbacUsersListEl.innerHTML = users.map((u) => `
            <div class="code-list-item">
                <span class="code-list-icon">${this.escapeHtml(u.role || '-')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(u.name || u.user_id)}</span>
                    <span class="code-list-meta">ID: ${this.escapeHtml(u.user_id)} | ${this.escapeHtml(this.formatDateTime(u.created_at))}</span>
                </span>
                <button class="btn-secondary btn-panel-action" data-rbac-delete="${this.escapeHtml(u.user_id)}" title="Eliminar">${CODE_ICONS.trash}</button>
            </div>
        `).join('');
    }

    renderRbacPolicies() {
        if (!this.rbacPoliciesListEl) return;
        const policies = this.rbacPolicies;
        if (policies.length === 0) {
            this.rbacPoliciesListEl.innerHTML = '<div class="code-empty-state">Sin politicas activas.</div>';
            return;
        }
        this.rbacPoliciesListEl.innerHTML = policies.map((p) => `
            <div class="code-list-item">
                <span class="code-list-icon">${this.escapeHtml(p.action || p.rule_type || '-')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(p.description || p.rule_id || '-')}</span>
                    <span class="code-list-meta">scope: ${this.escapeHtml(p.scope || '-')} | target: ${this.escapeHtml(p.target || '-')}</span>
                </span>
                <button class="btn-secondary btn-panel-action" data-policy-delete="${this.escapeHtml(p.rule_id)}" title="Eliminar">${CODE_ICONS.trash}</button>
            </div>
        `).join('');
    }

    renderEthicalRestrictions() {
        if (!this.ethicalRestrictionsListEl) return;
        const items = this.ethicalRestrictions;
        if (items.length === 0) {
            this.ethicalRestrictionsListEl.innerHTML = '<div class="code-empty-state">Sin restricciones eticas configuradas.</div>';
            return;
        }
        this.ethicalRestrictionsListEl.innerHTML = items.map((r) => `
            <div class="code-list-item">
                <span class="code-list-icon">${r.enabled ? CODE_ICONS.check : CODE_ICONS.cross}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(r.description || r.rule_id || '-')}</span>
                    <span class="code-list-meta">ID: ${this.escapeHtml(r.rule_id || '-')} | severity: ${this.escapeHtml(r.severity || '-')}</span>
                </span>
            </div>
        `).join('');
    }

    renderAuditStats() {
        if (!this.auditStatsSummaryEl) return;
        const stats = this.auditStats;
        if (!stats) {
            this.auditStatsSummaryEl.innerHTML = '<div class="code-empty-state">Sin estadisticas de auditoria.</div>';
            return;
        }
        const cards = [
            ['Total eventos', stats.total_events || 0],
            ['Acciones', stats.total_actions || 0],
            ['Denegaciones', stats.denied_count || 0],
            ['Aprobaciones', stats.approved_count || 0],
            ['Alertas', stats.alert_count || 0],
            ['Ultimo evento', this.formatDateTime(stats.last_event_at)],
        ];
        this.auditStatsSummaryEl.innerHTML = cards.map(([label, value]) =>
            `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`
        ).join('');
    }

    renderAuditRecent() {
        if (!this.auditRecentListEl) return;
        const entries = this.auditRecent;
        if (entries.length === 0) {
            this.auditRecentListEl.innerHTML = '<div class="code-empty-state">Sin eventos de auditoria recientes.</div>';
            return;
        }
        this.auditRecentListEl.innerHTML = entries.map((e) => `
            <div class="code-list-item">
                <span class="code-list-icon">${this.escapeHtml(e.action || e.event_type || '-')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(e.description || e.detail || '-')}</span>
                    <span class="code-list-meta">${this.escapeHtml(this.formatDateTime(e.timestamp || e.created_at))} | user: ${this.escapeHtml(e.user_id || '-')} | result: ${this.escapeHtml(e.result || '-')}</span>
                </span>
            </div>
        `).join('');
    }

    renderSandboxStatus() {
        if (!this.sandboxStatusInfoEl) return;
        const status = this.sandboxStatus;
        if (!status) {
            this.sandboxStatusInfoEl.textContent = 'Sandbox no disponible o sin estado.';
            return;
        }
        const parts = [
            `Habilitado: ${status.enabled ? 'si' : 'no'}`,
            status.mode ? `Modo: ${status.mode}` : null,
            status.allowed_commands_count != null ? `Comandos permitidos: ${status.allowed_commands_count}` : null,
            status.blocked_paths_count != null ? `Paths bloqueados: ${status.blocked_paths_count}` : null,
        ].filter(Boolean);
        this.sandboxStatusInfoEl.textContent = parts.join(' | ') || 'Sandbox activo.';
    }

    renderRateLimits() {
        if (!this.rateLimitsInfoEl) return;
        const limits = this.rateLimits;
        if (!limits) {
            this.rateLimitsInfoEl.innerHTML = '<div class="code-empty-state">Sin informacion de rate limits.</div>';
            return;
        }
        const entries = Array.isArray(limits.limits) ? limits.limits : (typeof limits === 'object' ? Object.entries(limits).filter(([k]) => k !== 'ok') : []);
        if (Array.isArray(entries) && entries.length > 0 && Array.isArray(entries[0])) {
            this.rateLimitsInfoEl.innerHTML = entries.map(([key, val]) =>
                `<div class="code-summary-card"><div class="code-summary-label">${this.escapeHtml(key)}</div><div class="code-summary-value">${this.escapeHtml(typeof val === 'object' ? JSON.stringify(val) : String(val))}</div></div>`
            ).join('');
        } else if (entries.length > 0) {
            this.rateLimitsInfoEl.innerHTML = entries.map((l) =>
                `<div class="code-summary-card"><div class="code-summary-label">${this.escapeHtml(l.key || l.name || '-')}</div><div class="code-summary-value">${this.escapeHtml(l.current || 0)}/${this.escapeHtml(l.limit || '-')} | window: ${this.escapeHtml(l.window || '-')}</div></div>`
            ).join('');
        } else {
            this.rateLimitsInfoEl.innerHTML = '<div class="code-empty-state">Sin rate limits activos.</div>';
        }
    }

    async addRbacUser() {
        const userId = this.rbacNewUserIdInput?.value?.trim();
        const userName = this.rbacNewUserNameInput?.value?.trim();
        const role = this.rbacNewUserRoleSelect?.value || 'viewer';
        if (!userId) return;
        try {
            await this.requestJson('/security/rbac/users', {
                method: 'POST',
                body: { user_id: userId, name: userName || userId, role },
            });
            if (this.rbacNewUserIdInput) this.rbacNewUserIdInput.value = '';
            if (this.rbacNewUserNameInput) this.rbacNewUserNameInput.value = '';
            await this.loadSecurity();
        } catch (error) {
            window.alert(error.message || 'No se pudo agregar el usuario.');
        }
    }

    async handleRbacUserClick(event) {
        const btn = event.target.closest('[data-rbac-delete]');
        if (!btn) return;
        const userId = btn.dataset.rbacDelete;
        if (!userId || !window.confirm(`Eliminar usuario RBAC "${userId}"?`)) return;
        try {
            await this.requestJson(`/security/rbac/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
            await this.loadSecurity();
        } catch (error) {
            window.alert(error.message || 'No se pudo eliminar el usuario.');
        }
    }

    async handleRbacPolicyClick(event) {
        const btn = event.target.closest('[data-policy-delete]');
        if (!btn) return;
        const ruleId = btn.dataset.policyDelete;
        if (!ruleId || !window.confirm(`Eliminar politica "${ruleId}"?`)) return;
        try {
            await this.requestJson(`/security/rbac/policies/${encodeURIComponent(ruleId)}`, { method: 'DELETE' });
            await this.loadSecurity();
        } catch (error) {
            window.alert(error.message || 'No se pudo eliminar la politica.');
        }
    }

    async exportAudit(fmt) {
        try {
            const response = await fetch(`${this.apiBase}/security/audit/export/${fmt}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `audit_export.${fmt}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (error) {
            window.alert(error.message || 'No se pudo exportar la auditoria.');
        }
    }

    // ── Analytics Tab ────────────────────────────────────────────────────

    async loadAnalytics() {
        try {
            const hours = Number(this.analyticsPeriodSelect?.value || 24);
            const [dashboardData, tokensData, errorsData, timeDistData, timelineData, goalsData, dagsData] = await Promise.all([
                this.fetchJson('/analytics/dashboard', { hours }).catch(() => null),
                this.fetchJson('/analytics/tokens', { hours }).catch(() => null),
                this.fetchJson('/analytics/errors', { hours, limit: 30 }).catch(() => ({ errors: [] })),
                this.fetchJson('/analytics/time-distribution', { hours }).catch(() => ({ distribution: [] })),
                this.fetchJson('/analytics/timeline', { hours }).catch(() => ({ timeline: [] })),
                this.fetchJson('/goals').catch(() => ({ goals: [] })),
                this.fetchJson('/dag/list').catch(() => ({ dags: [] })),
            ]);
            this.analyticsLoaded = true;
            this.analyticsDashboard = dashboardData;
            this.analyticsTokens = tokensData;
            this.analyticsErrors = Array.isArray(errorsData.errors) ? errorsData.errors : [];
            this.analyticsTimeDistribution = Array.isArray(timeDistData.distribution) ? timeDistData.distribution : [];
            this.analyticsTimeline = Array.isArray(timelineData.timeline) ? timelineData.timeline : [];
            this.goals = Array.isArray(goalsData.goals) ? goalsData.goals : [];
            this.dags = Array.isArray(dagsData.dags) ? dagsData.dags : [];
            this.renderAnalyticsDashboard();
            this.renderAnalyticsTokens();
            this.renderAnalyticsErrors();
            this.renderAnalyticsTimeDistribution();
            this.renderAnalyticsTimeline();
            this.renderGoals();
            this.renderDags();
        } catch (error) {
            // silently handle
        }
    }

    renderAnalyticsDashboard() {
        if (!this.analyticsDashboardSummaryEl) return;
        const d = this.analyticsDashboard;
        if (!d) {
            this.analyticsDashboardSummaryEl.innerHTML = '<div class="code-empty-state">Sin datos de analytics.</div>';
            return;
        }
        const cards = [
            ['Tareas completadas', d.tasks_completed || 0],
            ['Tareas fallidas', d.tasks_failed || 0],
            ['Tokens totales', this.formatTokenCount(d.total_tokens)],
            ['Costo total', this.formatUsd(d.total_cost_usd)],
            ['Proveedor top', d.top_provider || '-'],
            ['Modelo top', d.top_model || '-'],
            ['Tiempo promedio', d.avg_duration_seconds != null ? `${Number(d.avg_duration_seconds).toFixed(1)}s` : '-'],
            ['Errores', d.total_errors || 0],
        ];
        this.analyticsDashboardSummaryEl.innerHTML = cards.map(([label, value]) =>
            `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`
        ).join('');
    }

    renderAnalyticsTokens() {
        if (!this.analyticsTokensSummaryEl) return;
        const t = this.analyticsTokens;
        if (!t) {
            this.analyticsTokensSummaryEl.innerHTML = '<div class="code-empty-state">Sin datos de tokens.</div>';
            if (this.analyticsTokensByProviderEl) this.analyticsTokensByProviderEl.innerHTML = '';
            return;
        }
        const cards = [
            ['Tokens entrada', this.formatTokenCount(t.total_tokens_in)],
            ['Tokens salida', this.formatTokenCount(t.total_tokens_out)],
            ['Tokens total', this.formatTokenCount(t.total_tokens)],
            ['Costo total', this.formatUsd(t.total_cost_usd)],
            ['Eventos', t.event_count || 0],
        ];
        this.analyticsTokensSummaryEl.innerHTML = cards.map(([label, value]) =>
            `<div class="code-summary-card"><div class="code-summary-label">${label}</div><div class="code-summary-value">${this.escapeHtml(String(value))}</div></div>`
        ).join('');
        if (this.analyticsTokensByProviderEl) {
            const providers = Array.isArray(t.by_provider) ? t.by_provider : [];
            if (providers.length === 0) {
                this.analyticsTokensByProviderEl.innerHTML = '<div class="code-empty-state">Sin desglose por proveedor.</div>';
            } else {
                this.analyticsTokensByProviderEl.innerHTML = providers.map((p) => `
                    <div class="code-list-item">
                        <span class="code-list-icon">${this.escapeHtml(p.provider || '-')}</span>
                        <span class="code-list-stack">
                            <span class="code-list-text">${this.escapeHtml(p.model || '-')} | ${this.escapeHtml(this.formatUsd(p.total_cost_usd))}</span>
                            <span class="code-list-meta">tokens: ${this.escapeHtml(this.formatTokenCount(p.total_tokens))} | eventos: ${this.escapeHtml(String(p.event_count || 0))}</span>
                        </span>
                    </div>
                `).join('');
            }
        }
    }

    renderAnalyticsErrors() {
        if (!this.analyticsErrorsListEl) return;
        if (this.analyticsErrors.length === 0) {
            this.analyticsErrorsListEl.innerHTML = '<div class="code-empty-state">Sin errores recientes.</div>';
            return;
        }
        this.analyticsErrorsListEl.innerHTML = this.analyticsErrors.map((e) => `
            <div class="code-list-item is-error">
                <span class="code-list-icon">${this.escapeHtml(e.error_type || e.task_type || 'error')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(e.error || e.message || '-')}</span>
                    <span class="code-list-meta">${this.escapeHtml(this.formatDateTime(e.timestamp || e.created_at))} | provider: ${this.escapeHtml(e.provider || '-')} | model: ${this.escapeHtml(e.model || '-')}</span>
                </span>
            </div>
        `).join('');
    }

    renderAnalyticsTimeDistribution() {
        if (!this.analyticsTimeDistributionEl) return;
        if (this.analyticsTimeDistribution.length === 0) {
            this.analyticsTimeDistributionEl.innerHTML = '<div class="code-empty-state">Sin datos de distribucion de tiempo.</div>';
            return;
        }
        this.analyticsTimeDistributionEl.innerHTML = this.analyticsTimeDistribution.map((d) => `
            <div class="code-list-item">
                <span class="code-list-icon">${this.escapeHtml(d.category || d.task_type || '-')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(String(d.total_duration_seconds != null ? `${Number(d.total_duration_seconds).toFixed(1)}s` : '-'))} | ${this.escapeHtml(String(d.count || 0))} tareas</span>
                    <span class="code-list-meta">promedio: ${this.escapeHtml(d.avg_duration_seconds != null ? `${Number(d.avg_duration_seconds).toFixed(1)}s` : '-')} | share: ${this.escapeHtml(String(d.share_percent || 0))}%</span>
                </span>
            </div>
        `).join('');
    }

    renderAnalyticsTimeline() {
        if (!this.analyticsTimelineEl) return;
        if (this.analyticsTimeline.length === 0) {
            this.analyticsTimelineEl.innerHTML = '<div class="code-empty-state">Sin actividad reciente.</div>';
            return;
        }
        this.analyticsTimelineEl.innerHTML = this.analyticsTimeline.map((t) => `
            <div class="code-list-item">
                <span class="code-list-icon">${this.escapeHtml(t.hour || t.label || '-')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">tareas: ${this.escapeHtml(String(t.task_count || 0))} | tokens: ${this.escapeHtml(this.formatTokenCount(t.total_tokens))}</span>
                    <span class="code-list-meta">costo: ${this.escapeHtml(this.formatUsd(t.total_cost_usd))} | errores: ${this.escapeHtml(String(t.error_count || 0))}</span>
                </span>
            </div>
        `).join('');
    }

    renderGoals() {
        if (!this.goalsListEl) return;
        if (this.goals.length === 0) {
            this.goalsListEl.innerHTML = '<div class="code-empty-state">Sin objetivos activos.</div>';
            return;
        }
        this.goalsListEl.innerHTML = this.goals.map((g) => {
            const progress = Number(g.progress_percent || 0);
            const kpiCount = Array.isArray(g.kpis) ? g.kpis.length : 0;
            const taskCount = Array.isArray(g.tasks) ? g.tasks.length : 0;
            return `
                <div class="code-list-item">
                    <span class="code-list-icon">${this.escapeHtml(g.status || 'active')}</span>
                    <span class="code-list-stack">
                        <span class="code-list-text">${this.escapeHtml(g.title || g.goal_id || '-')} — ${progress}%</span>
                        <span class="code-list-meta">deadline: ${this.escapeHtml(g.deadline || '-')} | KPIs: ${kpiCount} | tareas: ${taskCount}</span>
                        <div style="background:rgba(255,255,255,0.08);border-radius:3px;height:4px;margin-top:3px;overflow:hidden"><div style="width:${Math.min(progress, 100)}%;height:100%;background:linear-gradient(90deg,#6366f1,#38bdf8);border-radius:3px"></div></div>
                    </span>
                    <button class="btn-secondary btn-panel-action" data-goal-delete="${this.escapeHtml(g.goal_id)}" title="Eliminar">${CODE_ICONS.trash}</button>
                </div>
            `;
        }).join('');
    }

    renderDags() {
        if (!this.dagListEl) return;
        if (this.dags.length === 0) {
            this.dagListEl.innerHTML = '<div class="code-empty-state">Sin DAGs creados.</div>';
            return;
        }
        this.dagListEl.innerHTML = this.dags.map((d) => `
            <div class="code-list-item">
                <span class="code-list-icon">${this.escapeHtml(d.status || '-')}</span>
                <span class="code-list-stack">
                    <span class="code-list-text">${this.escapeHtml(d.name || d.dag_id || '-')}</span>
                    <span class="code-list-meta">nodos: ${this.escapeHtml(String(d.node_count || 0))} | creado: ${this.escapeHtml(this.formatDateTime(d.created_at))}</span>
                </span>
            </div>
        `).join('');
    }

    async addGoal() {
        const title = this.goalNewTitleInput?.value?.trim();
        if (!title) return;
        const deadline = this.goalNewDeadlineInput?.value || null;
        try {
            await this.requestJson('/goals', {
                method: 'POST',
                body: { title, deadline },
            });
            if (this.goalNewTitleInput) this.goalNewTitleInput.value = '';
            if (this.goalNewDeadlineInput) this.goalNewDeadlineInput.value = '';
            await this.loadAnalytics();
        } catch (error) {
            window.alert(error.message || 'No se pudo crear el objetivo.');
        }
    }

    async handleGoalClick(event) {
        const btn = event.target.closest('[data-goal-delete]');
        if (!btn) return;
        const goalId = btn.dataset.goalDelete;
        if (!goalId || !window.confirm(`Eliminar objetivo "${goalId}"?`)) return;
        try {
            await this.requestJson(`/goals/${encodeURIComponent(goalId)}`, { method: 'DELETE' });
            await this.loadAnalytics();
        } catch (error) {
            window.alert(error.message || 'No se pudo eliminar el objetivo.');
        }
    }

    async generateWeeklyReport() {
        if (!this.analyticsWeeklyReportEl) return;
        try {
            this.analyticsWeeklyReportEl.textContent = 'Generando reporte semanal...';
            const data = await this.fetchJson('/analytics/report/weekly');
            if (!data || (!data.summary && !data.report)) {
                this.analyticsWeeklyReportEl.textContent = 'No hay datos suficientes para el reporte semanal.';
                return;
            }
            const report = data.summary || data.report || data;
            if (typeof report === 'string') {
                this.analyticsWeeklyReportEl.textContent = report;
            } else {
                const parts = [
                    report.window_label ? `Ventana: ${report.window_label}` : null,
                    report.total_cost_usd != null ? `Costo total: ${this.formatUsd(report.total_cost_usd)}` : null,
                    report.total_tokens != null ? `Tokens: ${this.formatTokenCount(report.total_tokens)}` : null,
                    report.event_count != null ? `Eventos: ${report.event_count}` : null,
                    report.top_provider ? `Top proveedor: ${report.top_provider}` : null,
                ].filter(Boolean);
                this.analyticsWeeklyReportEl.textContent = parts.join(' | ') || JSON.stringify(report, null, 2);
            }
        } catch (error) {
            this.analyticsWeeklyReportEl.textContent = error.message || 'No se pudo generar el reporte.';
        }
    }

    // ── Utilities ────────────────────────────────────────────────────────

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text || '');
        return div.innerHTML;
    }
}

window.codeManager = new CodeManager();
