/**
 * G-Mini Agent — Settings Module
 * Panel de configuración: providers, modelos, API keys, temperature.
 */

const BACKEND_API = 'http://127.0.0.1:8765/api';

// Iconos SVG inline (reemplazan dingbats/emojis para una UI consistente y profesional).
const SETTINGS_ICONS = {
    close: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    edit: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>',
};

// ── Catálogo de modelos — se carga dinámicamente desde data/models.yaml vía backend ──
// Estos objetos se rellenan en _loadModelsCatalog(); sirven de fallback vacío hasta entonces.
let PROVIDER_LABELS = {};
let MODEL_OPTIONS = {};
let GOOGLE_IMAGE_MODELS = [];
let GOOGLE_VIDEO_MODELS = [];
let GOOGLE_MUSIC_MODELS = [];

// ── Modelos con soporte de computer use por proveedor (sub-agente dedicado) ──
const COMPUTER_USE_MODELS = {
    google: ['gemini-2.5-computer-use-preview-10-2025'],
    anthropic: ['claude-sonnet-4-6', 'claude-opus-4-6'],
    openai: ['computer-use-preview'],
};
const COMPUTER_USE_PROVIDER_LABELS = { google: 'Google', anthropic: 'Anthropic', openai: 'OpenAI' };

class SettingsManager {
    constructor() {
        this.panel = document.getElementById('settings-panel');
        this.modeSelect = document.getElementById('select-mode');
        this.modeSummary = document.getElementById('mode-summary');
        this.customModeKeyInput = document.getElementById('input-custom-mode-key');
        this.customModeNameInput = document.getElementById('input-custom-mode-name');
        this.customModeIconInput = document.getElementById('input-custom-mode-icon');
        this.customModeDescriptionInput = document.getElementById('input-custom-mode-description');
        this.customModeBehaviorInput = document.getElementById('input-custom-mode-behavior');
        this.customModeSystemInput = document.getElementById('input-custom-mode-system');
        this.customModeAllowedInput = document.getElementById('input-custom-mode-allowed');
        this.customModeRestrictedInput = document.getElementById('input-custom-mode-restricted');
        this.customModeScopeCheckbox = document.getElementById('cb-custom-mode-scope');
        this.customModeMeta = document.getElementById('custom-mode-meta');
        this.customModeSaveBtn = document.getElementById('btn-save-custom-mode');
        this.customModeDeleteBtn = document.getElementById('btn-delete-custom-mode');
        this.promptSelect = document.getElementById('select-prompt-template');
        this.promptInput = document.getElementById('input-prompt-template');
        this.promptMeta = document.getElementById('prompt-template-meta');
        this.promptSaveBtn = document.getElementById('btn-save-prompt-template');
        this.promptResetBtn = document.getElementById('btn-reset-prompt-template');
        this.appStartWithWindowsCheckbox = document.getElementById('cb-app-start-with-windows');
        this.appMinimizeToTrayCheckbox = document.getElementById('cb-app-minimize-to-tray');
        this.appCloseToTrayCheckbox = document.getElementById('cb-app-close-to-tray');
        this.appStartHiddenToTrayCheckbox = document.getElementById('cb-app-start-hidden-to-tray');
        this.appBehaviorMeta = document.getElementById('app-behavior-meta');
        this.appBehaviorSaveBtn = document.getElementById('btn-save-app-behavior');
        this.autonomySelect = document.getElementById('select-autonomy');
        this.autonomyScopeMeta = document.getElementById('autonomy-scope-meta');
        this.autonomyLevelSelect = document.getElementById('select-autonomy-level');  // gate de permisos (asistido/supervisado/libre)
        this.autonomyMeta = document.getElementById('autonomy-meta');
        this.blockedSitesEnabledCheckbox = document.getElementById('cb-blocked-sites-enabled');
        this.blockedSitesInput = document.getElementById('input-blocked-sites');
        this.blockedSitesMeta = document.getElementById('blocked-sites-meta');
        this.blockedSitesSaveBtn = document.getElementById('btn-save-blocked-sites');
        this.execApprovalsEnabledCheckbox = document.getElementById('cb-exec-approvals-enabled');
        this.execApprovalsHostInput = document.getElementById('input-exec-approvals-host');
        this.execAllowedCommandsInput = document.getElementById('input-exec-allowed-commands');
        this.execAllowedPatternsInput = document.getElementById('input-exec-allowed-patterns');
        this.execDeniedPatternsInput = document.getElementById('input-exec-denied-patterns');
        this.execApprovalsMeta = document.getElementById('exec-approvals-meta');
        this.execApprovalsSaveBtn = document.getElementById('btn-save-exec-approvals');
        this.mcpEnabledCheckbox = document.getElementById('cb-mcp-enabled');
        this.mcpMeta = document.getElementById('mcp-meta');
        this.mcpAddMeta = document.getElementById('mcp-add-meta');
        this.mcpServersList = document.getElementById('mcp-servers-list');
        this.mcpAddServerBtn = document.getElementById('btn-mcp-add-server');
        this.mcpTransportSelect = document.getElementById('select-mcp-transport');
        this.mcpIntegrationModeSelect = document.getElementById('select-mcp-integration-mode');
        this.gatewayTelegramEnabledCheckbox = document.getElementById('cb-gateway-telegram-enabled');
        this.gatewayTelegramDefaultChatInput = document.getElementById('input-gateway-telegram-default-chat');
        this.gatewayTelegramAllowedChatsInput = document.getElementById('input-gateway-telegram-allowed-chat-ids');
        this.gatewayTelegramTokenInput = document.getElementById('input-gateway-telegram-token');
        this.gatewayTelegramMeta = document.getElementById('gateway-telegram-meta');
        this.gatewayTelegramSaveBtn = document.getElementById('btn-save-gateway-telegram');
        this.gatewayTelegramSaveTokenBtn = document.getElementById('btn-save-gateway-telegram-token');
        this.gatewayWhatsAppEnabledCheckbox = document.getElementById('cb-gateway-whatsapp-enabled');
        this.gatewayWhatsAppDefaultChatInput = document.getElementById('input-gateway-whatsapp-default-chat');
        this.gatewayWhatsAppAllowedChatsInput = document.getElementById('input-gateway-whatsapp-allowed-chat-ids');
        this.gatewayWhatsAppSessionNameInput = document.getElementById('input-gateway-whatsapp-session-name');
        this.gatewayWhatsAppMeta = document.getElementById('gateway-whatsapp-meta');
        this.gatewayWhatsAppQrImage = document.getElementById('gateway-whatsapp-qr');
        this.gatewayWhatsAppSaveBtn = document.getElementById('btn-save-gateway-whatsapp');
        this.gatewayDiscordEnabledCheckbox = document.getElementById('cb-gateway-discord-enabled');
        this.gatewayDiscordDefaultChannelInput = document.getElementById('input-gateway-discord-default-channel');
        this.gatewayDiscordAllowedGuildsInput = document.getElementById('input-gateway-discord-allowed-guild-ids');
        this.gatewayDiscordAllowedChannelsInput = document.getElementById('input-gateway-discord-allowed-channel-ids');
        this.gatewayDiscordTokenInput = document.getElementById('input-gateway-discord-token');
        this.gatewayDiscordMeta = document.getElementById('gateway-discord-meta');
        this.gatewayDiscordSaveBtn = document.getElementById('btn-save-gateway-discord');
        this.gatewayDiscordSaveTokenBtn = document.getElementById('btn-save-gateway-discord-token');
        this.skillsEnabledCheckbox = document.getElementById('cb-skills-enabled');
        this.skillsPreferredInput = document.getElementById('input-skills-preferred');
        this.skillsPathsInput = document.getElementById('input-skills-paths');
        this.skillsMeta = document.getElementById('skills-meta');
        this.skillsSaveBtn = document.getElementById('btn-save-skills');
        this.schedulerEnabledCheckbox = document.getElementById('cb-scheduler-enabled');
        this.schedulerPollIntervalInput = document.getElementById('input-scheduler-poll-interval');
        this.schedulerSettingsMeta = document.getElementById('scheduler-settings-meta');
        this.schedulerSettingsSaveBtn = document.getElementById('btn-save-scheduler-settings');
        this.budgetEnabledCheckbox = document.getElementById('cb-budget-enabled');
        this.budgetDailyLimitInput = document.getElementById('input-budget-daily-limit');
        this.budgetMonthlyLimitInput = document.getElementById('input-budget-monthly-limit');
        this.budgetWarningThresholdInput = document.getElementById('input-budget-warning-threshold');
        this.budgetTaskLimitInput = document.getElementById('input-budget-task-limit');
        this.budgetSubagentLimitInput = document.getElementById('input-budget-subagent-limit');
        this.budgetSubagentShareInput = document.getElementById('input-budget-subagent-share');
        this.budgetModeLimitsInput = document.getElementById('input-budget-mode-limits');
        this.budgetMeta = document.getElementById('budget-meta');
        this.budgetSaveBtn = document.getElementById('btn-save-budget');
        this.paymentsEnabledCheckbox = document.getElementById('cb-payments-enabled');
        this.spendPermissionsModeSelect = document.getElementById('select-spend-permissions-mode');
        this.spendAskAboveInput = document.getElementById('input-spend-ask-above');
        this.spendAutoApproveUnderInput = document.getElementById('input-spend-auto-approve-under');
        this.paymentsMeta = document.getElementById('payments-meta');
        this.paymentsSaveBtn = document.getElementById('btn-save-payments');
        this.paymentsDefaultAccountInput = document.getElementById('input-payments-default-account');
        this.paymentsAccountsInput = document.getElementById('input-payments-accounts');
        this.paymentsAccountsMeta = document.getElementById('payments-accounts-meta');
        this.paymentsAccountsSaveBtn = document.getElementById('btn-save-payment-accounts');
        this.providerSelect = document.getElementById('select-provider');
        this.modelSelect = document.getElementById('select-model');
        this.tempSlider = document.getElementById('input-temperature');
        this.tempValue = document.getElementById('temp-value');
        this.keysContainer = document.getElementById('api-keys-container');
        this.voiceTtsSelect = document.getElementById('select-tts-engine');
        this.voiceRuntimeStatus = document.getElementById('voice-runtime-status');
        this.voiceGoogleConfig = document.getElementById('voice-google-config');
        this.voiceGoogleVoiceConfig = document.getElementById('voice-google-voice-config');
        this.voiceGoogleVoiceSelect = document.getElementById('select-google-voice');
        this.voiceGoogleKeyInput = document.getElementById('input-voice-google-api-key');
        this.voiceGoogleKeyToggleBtn = document.getElementById('btn-toggle-voice-google-key');
        this.voiceGoogleKeySaveBtn = document.getElementById('btn-save-voice-google-key');
        this.voiceGoogleKeyStatus = document.getElementById('key-status-voice-google');
        this.voiceWebspeechConfig = document.getElementById('voice-webspeech-config');
        this.voiceWebspeechVoiceSelect = document.getElementById('select-webspeech-voice');
        this.voiceElevenlabsConfig = document.getElementById('voice-elevenlabs-config');
        this.voiceElevenlabsKeyInput = document.getElementById('input-voice-elevenlabs-api-key');
        this.voiceElevenlabsKeyToggleBtn = document.getElementById('btn-toggle-voice-elevenlabs-key');
        this.voiceElevenlabsKeySaveBtn = document.getElementById('btn-save-voice-elevenlabs-key');
        this.voiceElevenlabsKeyStatus = document.getElementById('key-status-voice-elevenlabs');
        this.voiceElevenlabsVoiceIdInput = document.getElementById('input-elevenlabs-voice-id');
        this.voiceSpeedSlider = document.getElementById('input-tts-speed');
        this.voiceSpeedValue = document.getElementById('tts-speed-value');
        this.voiceSpeedHelp = document.getElementById('voice-speed-help');
        this.voiceMeta = document.getElementById('voice-character-meta');
        this.currentMode = 'normal';
        this.availableModes = [];
        this.availablePromptTemplates = [];
        this.currentProvider = 'openai';
        this.currentModel = 'gpt-5.4';
        this.currentStartWithWindows = false;
        this.currentMinimizeToTray = true;
        this.currentCloseToTray = true;
        this.currentStartHiddenToTray = false;
        this.appRuntimeState = null;
        this.currentAutonomy = 'media';
        this.currentAutonomyLevel = 'supervisado';
        this.currentExecApprovalHostKey = 'default-host';
        this.execApprovalsProfiles = {};
        this.currentMcpServers = [];
        this.mcpRuntimeServers = [];
        this._mcpToolsCache = {};
        this.currentGatewayChannels = {};
        this.currentGatewayTelegramCredential = { configured: false, masked: null };
        this.currentGatewayDiscordCredential = { configured: false, masked: null };
        this.gatewayRuntimeStatus = null;
        this.currentGatewayWhatsAppRuntime = {};
        this.currentGatewayDiscordRuntime = {};
        this.currentSkillsPreferred = [];
        this.currentSkillsPaths = [];
        this.discoveredSkills = [];
        this.skillRoots = [];
        this.currentSchedulerEnabled = true;
        this.currentSchedulerPollInterval = 2.0;
        this.currentModelRouterHardLimits = {};
        this.currentBudgetEnabled = true;
        this.currentBudgetDailyLimit = 10.0;
        this.currentBudgetMonthlyLimit = 200.0;
        this.currentBudgetWarningThreshold = 80;
        this.currentTaskBudgetLimit = 5.0;
        this.currentBudgetSubagentLimit = 0.2;
        this.currentBudgetSubagentShare = 0.4;
        this.currentBudgetModeLimits = {};
        this.currentPaymentsEnabled = true;
        this.currentSpendPermissionsMode = 'ask_always';
        this.currentSpendAskAboveUsd = 25.0;
        this.currentSpendAutoApproveUnderUsd = 5.0;
        this.currentDefaultPaymentAccountId = '';
        this.currentPaymentAccounts = [];
        this.currentPage = 'general';
        this.currentModelAssignments = {};
        this.currentCrews = [];
        this.voiceMetadata = null;
        this.voiceApiKeyStatus = {};
        this.voiceDraft = null;
        this.voiceDraftDirty = false;
    }

    init() {
        document.getElementById('btn-settings').addEventListener('click', () => this.toggle());
        document.getElementById('btn-close-settings').addEventListener('click', () => this.hide());

        // ── Sidebar navigation ──
        this.panel.querySelectorAll('.settings-nav-item').forEach((btn) => {
            btn.addEventListener('click', () => this._switchPage(btn.dataset.page));
        });

        this.modeSelect.addEventListener('change', async (e) => {
            this.currentMode = e.target.value;
            await this._applyModeChange();
        });

        this.customModeSaveBtn.addEventListener('click', async () => {
            await this._saveCustomMode();
        });

        this.customModeDeleteBtn.addEventListener('click', async () => {
            await this._deleteCustomMode();
        });

        this.promptSelect.addEventListener('change', () => {
            this._renderSelectedPrompt();
        });

        this.promptSaveBtn.addEventListener('click', async () => {
            await this._savePromptTemplate();
        });

        this.promptResetBtn.addEventListener('click', async () => {
            await this._resetPromptTemplate();
        });

        this.appBehaviorSaveBtn?.addEventListener('click', async () => {
            await this._saveAppBehaviorSettings();
        });

        [
            this.appStartWithWindowsCheckbox,
            this.appMinimizeToTrayCheckbox,
            this.appCloseToTrayCheckbox,
            this.appStartHiddenToTrayCheckbox,
        ].forEach((element) => {
            element?.addEventListener('change', () => this._renderAppBehaviorMeta());
        });

        this.autonomySelect?.addEventListener('change', async (e) => {
            this.currentAutonomy = e.target.value;
            await this._saveConfigValue('agent', 'autonomy', this.currentAutonomy);
            this._renderAutonomyScopeMeta();
        });

        this.autonomyLevelSelect.addEventListener('change', async (e) => {
            this.currentAutonomyLevel = e.target.value;
            await this._saveConfigValue('agent', 'autonomy_level', this.currentAutonomyLevel);
            this._renderAutonomyMeta();
        });

        // ── Monitor selector ──
        const monitorSelect = document.getElementById('select-target-monitor');
        if (monitorSelect) {
            monitorSelect.addEventListener('change', async (e) => {
                const val = parseInt(e.target.value, 10) || 0;
                await this._saveTargetMonitor(val);
            });
        }
        const btnRefreshMonitors = document.getElementById('btn-refresh-monitors');
        if (btnRefreshMonitors) {
            btnRefreshMonitors.addEventListener('click', async () => {
                await this._syncMonitors();
            });
        }

        this.blockedSitesEnabledCheckbox.addEventListener('change', async (e) => {
            await this._saveConfigValue('agent', 'blocked_sites_enabled', e.target.checked);
            this._renderBlockedSitesMeta();
        });

        this.blockedSitesSaveBtn.addEventListener('click', async () => {
            const sites = this._parseMultilineList(this.blockedSitesInput.value);
            await this._saveConfigValue('agent', 'blocked_sites', sites);
            this.blockedSitesInput.value = sites.join('\n');
            this._renderBlockedSitesMeta();
        });

        this.execApprovalsEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveConfigValue('terminals', 'exec_approvals_enabled', e.target.checked);
            this._renderExecApprovalsMeta();
        });

        this.execApprovalsSaveBtn?.addEventListener('click', async () => {
            await this._saveExecApprovals();
        });

        this.mcpEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveConfigValue('mcp', 'enabled', e.target.checked);
            await this._syncMcpRuntime();
        });

        this.mcpIntegrationModeSelect?.addEventListener('change', async (e) => {
            await this._saveConfigValue('mcp', 'integration_mode', e.target.value);
        });

        // ── MCP Tabs ──
        this.panel.querySelectorAll('.mcp-tab').forEach((tab) => {
            tab.addEventListener('click', () => {
                this.panel.querySelectorAll('.mcp-tab').forEach((t) => t.classList.toggle('active', t === tab));
                this.panel.querySelectorAll('.mcp-tab-content').forEach((c) =>
                    c.classList.toggle('active', c.dataset.mcpTab === tab.dataset.mcpTab)
                );
            });
        });

        // ── MCP add server ──
        this.mcpAddServerBtn?.addEventListener('click', async () => {
            await this._addMcpServer();
        });

        this.gatewayTelegramEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveGatewayTelegramConfig();
            this.currentGatewayChannels = {
                ...(this.currentGatewayChannels || {}),
                telegram: {
                    ...(this.currentGatewayChannels?.telegram || {}),
                    enabled: !!e.target.checked,
                },
            };
            this._renderGatewayTelegramMeta();
        });

        this.gatewayTelegramSaveBtn?.addEventListener('click', async () => {
            await this._saveGatewayTelegramConfig();
        });

        this.gatewayTelegramSaveTokenBtn?.addEventListener('click', async () => {
            await this._saveGatewayTelegramCredential();
        });

        this.gatewayWhatsAppEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveGatewayWhatsAppConfig();
            this.currentGatewayChannels = {
                ...(this.currentGatewayChannels || {}),
                whatsapp: {
                    ...(this.currentGatewayChannels?.whatsapp || {}),
                    enabled: !!e.target.checked,
                },
            };
            this._renderGatewayWhatsAppMeta();
        });

        this.gatewayWhatsAppSaveBtn?.addEventListener('click', async () => {
            await this._saveGatewayWhatsAppConfig();
        });

        this.gatewayDiscordEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveGatewayDiscordConfig();
            this.currentGatewayChannels = {
                ...(this.currentGatewayChannels || {}),
                discord: {
                    ...(this.currentGatewayChannels?.discord || {}),
                    enabled: !!e.target.checked,
                },
            };
            this._renderGatewayDiscordMeta();
        });

        this.gatewayDiscordSaveBtn?.addEventListener('click', async () => {
            await this._saveGatewayDiscordConfig();
        });

        this.gatewayDiscordSaveTokenBtn?.addEventListener('click', async () => {
            await this._saveGatewayDiscordCredential();
        });

        this.skillsEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveConfigValue('skills', 'enabled', e.target.checked);
            this._renderSkillsMeta();
        });

        this.skillsSaveBtn?.addEventListener('click', async () => {
            await this._saveSkillsConfig();
        });

        this.schedulerEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveConfigValue('scheduler', 'enabled', e.target.checked);
            this.currentSchedulerEnabled = e.target.checked;
            this._renderSchedulerSettingsMeta();
        });

        this.schedulerSettingsSaveBtn?.addEventListener('click', async () => {
            await this._saveSchedulerSettings();
        });

        this.budgetEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveConfigValue('budget', 'enabled', e.target.checked);
            this.currentBudgetEnabled = e.target.checked;
            this._toggleBudgetFields();
            this._renderBudgetMeta();
        });

        this.budgetSaveBtn?.addEventListener('click', async () => {
            await this._saveBudgetSettings();
        });

        // ── Budget disabled-group toggle ──
        this._toggleBudgetFields();

        // ── JSON textarea real-time validation ──
        this._setupJsonValidation(this.budgetModeLimitsInput);
        this._setupJsonValidation(this.paymentsAccountsInput);

        // ── MCP transport clear fields ──
        this.mcpTransportSelect?.addEventListener('change', () => {
            const isStdio = this.mcpTransportSelect.value === 'stdio';
            const stdioFields = document.getElementById('mcp-fields-stdio');
            const remoteFields = document.getElementById('mcp-fields-remote');
            if (stdioFields) stdioFields.style.display = isStdio ? '' : 'none';
            if (remoteFields) remoteFields.style.display = isStdio ? 'none' : '';
            // Clear fields on transport switch
            if (isStdio) {
                const urlInput = document.getElementById('input-mcp-url');
                if (urlInput) urlInput.value = '';
            } else {
                ['input-mcp-command', 'input-mcp-args', 'input-mcp-cwd'].forEach((id) => {
                    const el = document.getElementById(id);
                    if (el) el.value = '';
                });
            }
        });

        // ── Model Assignments ──
        this.modelAssignmentsContainer = document.getElementById('model-assignments-container');
        this.modelAssignmentsMeta = document.getElementById('model-assignments-meta');
        document.getElementById('btn-save-model-assignments')?.addEventListener('click', async () => {
            await this._saveModelAssignments();
        });

        // ── Computer Use ──
        document.getElementById('btn-save-computer-use')?.addEventListener('click', async () => {
            await this._saveComputerUseConfig();
        });

        // ── Crews ──
        this.crewsList = document.getElementById('crews-list');
        this.crewsMeta = document.getElementById('crews-meta');
        this.crewFormMeta = document.getElementById('crew-form-meta');
        this.crewNameInput = document.getElementById('input-crew-name');
        this.crewProcessSelect = document.getElementById('select-crew-process');
        this.crewManagerModelInput = document.getElementById('input-crew-manager-model');
        this.crewRolesContainer = document.getElementById('crew-roles-container');

        this.crewProcessSelect?.addEventListener('change', () => {
            const isHierarchical = this.crewProcessSelect.value === 'hierarchical';
            const managerSection = document.getElementById('crew-manager-section');
            if (managerSection) managerSection.style.display = isHierarchical ? '' : 'none';
        });

        document.getElementById('btn-crew-add-role')?.addEventListener('click', () => {
            this._addCrewRoleEntry();
        });

        document.getElementById('btn-crew-save')?.addEventListener('click', async () => {
            await this._saveNewCrew();
        });

        this.panel.querySelectorAll('.crew-template-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                this._applyCrewTemplate(btn.dataset.template);
            });
        });

        this.paymentsEnabledCheckbox?.addEventListener('change', async (e) => {
            await this._saveConfigValue('payments', 'enabled', e.target.checked);
            this.currentPaymentsEnabled = e.target.checked;
            this._renderPaymentsMeta();
        });

        this.paymentsSaveBtn?.addEventListener('click', async () => {
            await this._savePaymentsSettings();
        });

        this.paymentsAccountsSaveBtn?.addEventListener('click', async () => {
            await this._savePaymentAccountsSettings();
        });

        [
            this.spendPermissionsModeSelect,
            this.spendAskAboveInput,
            this.spendAutoApproveUnderInput,
        ].forEach((element) => {
            element?.addEventListener('input', () => this._renderPaymentsMeta());
            element?.addEventListener('change', () => this._renderPaymentsMeta());
        });

        [
            this.paymentsDefaultAccountInput,
            this.paymentsAccountsInput,
        ].forEach((element) => {
            element?.addEventListener('input', () => this._renderPaymentAccountsMeta());
            element?.addEventListener('change', () => this._renderPaymentAccountsMeta());
        });

        this.providerSelect.addEventListener('change', (e) => {
            this.currentProvider = e.target.value;
            this._updateModelOptions();
            this._applyProviderChange();
            this._toggleGoogleBackendGroup();
        });

        this.modelSelect.addEventListener('change', (e) => {
            this.currentModel = e.target.value;
            this._updateLiveModelHint();
            this._applyModelChange();
        });

        this.tempSlider.addEventListener('input', (e) => {
            this.tempValue.textContent = e.target.value;
        });
        this.tempSlider.addEventListener('change', (e) => {
            ws.sendConfig('model_router', 'temperature', parseFloat(e.target.value));
        });

        // ── Google Backend selector ──
        const googleBackendSelect = document.getElementById('select-google-backend');
        if (googleBackendSelect) {
            googleBackendSelect.addEventListener('change', async (e) => {
                const isVertex = e.target.value === 'vertex_ai';
                const vertexConfig = document.getElementById('google-vertex-config');
                if (vertexConfig) vertexConfig.style.display = isVertex ? '' : 'none';
                if (!isVertex) {
                    await this._saveGoogleBackendConfig();
                }
            });
        }
        const btnSaveGoogleBackend = document.getElementById('btn-save-google-backend');
        if (btnSaveGoogleBackend) {
            btnSaveGoogleBackend.addEventListener('click', async () => {
                await this._saveGoogleBackendConfig();
            });
        }

        document.getElementById('cb-always-on-top').addEventListener('change', (e) => {
            if (window.gmini) window.gmini.toggleAlwaysOnTop(e.target.checked);
        });

        document.getElementById('cb-overlay').addEventListener('change', (e) => {
            if (window.gmini) window.gmini.toggleOverlay(e.target.checked);
        });

        // Modo de visualizacion (chat flotante vs avatar flotante)
        const selDisplayMode = document.getElementById('select-display-mode');
        selDisplayMode?.addEventListener('change', async (e) => {
            const mode = e.target.value === 'skin' ? 'skin' : 'chat';
            await this._saveConfigValue('character', 'mode', mode);
            if (window.gmini && typeof window.gmini.skinSetMode === 'function') {
                await window.gmini.skinSetMode(mode);
            }
        });

        // Skin del avatar flotante
        const selAvatarSkin = document.getElementById('select-avatar-skin');
        selAvatarSkin?.addEventListener('change', async (e) => {
            await this._saveConfigValue('character', 'skin', e.target.value);
        });

        // Tipo de personaje (3D / 2D / Sin personaje): filtra las skins disponibles
        const selCharType = document.getElementById('select-character-type');
        selCharType?.addEventListener('change', async (e) => {
            await this._populateAvatarSkinOptions(null, e.target.value);
        });

        this._setupCharacterCreator();

        // Voice & Character settings
        const voiceSaveBtn = document.getElementById('btn-save-voice');
        voiceSaveBtn?.addEventListener('click', async () => {
            await this._saveVoiceCharacterSettings();
        });
        this.voiceTtsSelect?.addEventListener('change', () => {
            this._updateVoiceDraft({ tts_primary: this.voiceTtsSelect.value || 'melotts' }, 'select-tts-engine:change');
            this._renderVoiceEngineState();
        });
        this.voiceSpeedSlider?.addEventListener('input', (e) => {
            if (this.voiceSpeedValue) this.voiceSpeedValue.textContent = e.target.value;
            const nextSpeed = Number.parseFloat(e.target.value || '1.0');
            this._updateVoiceDraft(
                { tts_speed: Number.isFinite(nextSpeed) ? nextSpeed : 1.0 },
                'tts-speed:input',
            );
            // Web Speech lee el rate de localStorage (no pasa por backend).
            try { localStorage.setItem('webspeech_rate', String(Number.isFinite(nextSpeed) ? nextSpeed : 1.0)); } catch (_) { /* noop */ }
        });
        this.voiceElevenlabsVoiceIdInput?.addEventListener('input', (e) => {
            this._updateVoiceDraft({ elevenlabs_voice_id: String(e.target.value || '') }, 'elevenlabs-voice-id:input');
        });
        this.voiceGoogleVoiceSelect?.addEventListener('change', () => {
            this._updateVoiceDraft({ google_voice: this.voiceGoogleVoiceSelect.value || 'Kore' }, 'google-voice:change');
        });
        // Voz Web Speech: se guarda en localStorage (la lee app.js al hablar). No va al backend.
        this.voiceWebspeechVoiceSelect?.addEventListener('change', () => {
            const val = this.voiceWebspeechVoiceSelect.value || '';
            try { localStorage.setItem('webspeech_voice', val); } catch (_) { /* noop */ }
        });
        if (typeof window.speechSynthesis !== 'undefined') {
            window.speechSynthesis.addEventListener('voiceschanged', () => this._populateWebspeechVoices());
        }
        document.getElementById('cb-auto-tts')?.addEventListener('change', (e) => {
            this._updateVoiceDraft({ auto_tts: !!e.target.checked }, 'auto-tts:change');
            this._renderVoiceEngineState();
        });
        document.getElementById('cb-voice-enabled')?.addEventListener('change', (e) => {
            this._updateVoiceDraft({ enabled: !!e.target.checked }, 'voice-enabled:change');
            this._renderVoiceEngineState();
        });
        this.voiceGoogleKeyToggleBtn?.addEventListener('click', () => {
            this._togglePasswordInput(this.voiceGoogleKeyInput, this.voiceGoogleKeyToggleBtn);
        });
        this.voiceElevenlabsKeyToggleBtn?.addEventListener('click', () => {
            this._togglePasswordInput(this.voiceElevenlabsKeyInput, this.voiceElevenlabsKeyToggleBtn);
        });
        this.voiceGoogleKeySaveBtn?.addEventListener('click', async () => {
            await this._saveVoiceApiKey('google', this.voiceGoogleKeyInput);
        });
        this.voiceElevenlabsKeySaveBtn?.addEventListener('click', async () => {
            await this._saveVoiceApiKey('elevenlabs', this.voiceElevenlabsKeyInput);
        });

        // Setup Generative Models Dropdowns
        const imageSelect = document.getElementById('select-image-model');
        const videoSelect = document.getElementById('select-video-model');
        const musicSelect = document.getElementById('select-music-model');
        
        if (imageSelect) {
            GOOGLE_IMAGE_MODELS.forEach(m => imageSelect.appendChild(new Option(m, m)));
        }
        if (videoSelect) {
            GOOGLE_VIDEO_MODELS.forEach(m => videoSelect.appendChild(new Option(m, m)));
        }
        if (musicSelect) {
            GOOGLE_MUSIC_MODELS.forEach(m => musicSelect.appendChild(new Option(m, m)));
        }

        const btnSaveGenerative = document.getElementById('btn-save-generative-models');
        if (btnSaveGenerative) {
            btnSaveGenerative.addEventListener('click', async () => {
                if (imageSelect) await this._saveConfigValue('generative_models', 'image_model', imageSelect.value);
                if (videoSelect) await this._saveConfigValue('generative_models', 'video_model', videoSelect.value);
                if (musicSelect) await this._saveConfigValue('generative_models', 'music_model', musicSelect.value);
                
                // Mostrar un feedback visual rápido
                const originalText = btnSaveGenerative.textContent;
                btnSaveGenerative.textContent = '¡Guardado!';
                setTimeout(() => btnSaveGenerative.textContent = originalText, 1500);
            });
        }

        this._updateModelOptions();
        this._renderApiKeys();
        // Cargar catálogo de modelos desde backend y luego sincronizar config
        setTimeout(async () => {
            await this._loadModelsCatalog();
            // Fire CU config load independently so it doesn't depend on full sync chain
            this._loadComputerUseConfig().catch(e => console.warn('CU config preload failed:', e));
            await this._syncFromBackend();
        }, 1500);
    }

    toggle() { this.panel.classList.toggle('hidden'); }
    show()   {
        this.panel.classList.remove('hidden');
        this._loadComputerUseConfig().catch(() => {});
    }
    hide()   { this.panel.classList.add('hidden'); }

    _switchPage(pageId) {
        this.currentPage = pageId;
        this.panel.querySelectorAll('.settings-nav-item').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.page === pageId);
        });
        this.panel.querySelectorAll('.settings-page').forEach((page) => {
            page.classList.toggle('active', page.dataset.page === pageId);
        });
    }

    _updateModelOptions() {
        // Soporta formato lista (otros proveedores) Y formato dict rico (Google)
        const raw = MODEL_OPTIONS[this.currentProvider] || [];
        const isDict = !Array.isArray(raw);
        const allModels = isDict ? Object.keys(raw) : raw;
        this.modelSelect.innerHTML = '';
        if (allModels.length === 0) {
            const opt = document.createElement('option');
            opt.textContent = '(auto-detect)';
            this.modelSelect.appendChild(opt);
            return;
        }
        allModels.forEach((m) => {
            const opt = document.createElement('option');
            opt.value = m;
            // Los modelos Live-only se marcan con "(Live)" — solo funcionan con el boton de voz RT
            const isLive = isDict && raw[m]?.api_method === 'live';
            opt.textContent = isLive ? `${m}  (Live)` : m;
            if (isLive) opt.style.color = '#7c3aed';  // morado para distinguirlos
            if (m === this.currentModel) opt.selected = true;
            this.modelSelect.appendChild(opt);
        });
        if (!allModels.includes(this.currentModel) && allModels.length > 0) {
            this.currentModel = allModels[0];
            this.modelSelect.value = this.currentModel;
        }
        // Mostrar aviso si el modelo seleccionado es Live-only
        this._updateLiveModelHint();
    }

    _updateLiveModelHint() {
        const raw = MODEL_OPTIONS[this.currentProvider] || [];
        const isDict = !Array.isArray(raw);
        const isLive = isDict && raw[this.currentModel]?.api_method === 'live';
        let hint = document.getElementById('live-model-hint');
        if (!hint && this.modelSelect) {
            hint = document.createElement('div');
            hint.id = 'live-model-hint';
            hint.className = 'live-model-hint';
            this.modelSelect.parentElement?.appendChild(hint);
        }
        if (hint) {
            hint.style.display = isLive ? '' : 'none';
            hint.textContent = isLive
                ? 'Modelo Live API — solo funciona con el boton de voz en tiempo real. No usa el chat de texto.'
                : '';
        }
    }

    _renderApiKeys() {
        this.keysContainer.innerHTML = '';

        for (const [provider, label] of Object.entries(PROVIDER_LABELS)) {
            const row = document.createElement('div');
            row.className = 'api-key-row';
            row.innerHTML = `
                <label>${label}</label>
                <div class="api-key-input-group">
                    <input type="password" class="api-key-input" data-provider="${provider}" 
                           placeholder="sk-... o clave API" autocomplete="off">
                    <button class="btn-toggle-key" data-provider="${provider}" title="Mostrar/ocultar"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
                </div>
                <span class="api-key-status" id="key-status-${provider}">—</span>
                <button class="btn-save-key" data-provider="${provider}">Guardar</button>
            `;
            this.keysContainer.appendChild(row);
        }

        // Toggle visibility
        this.keysContainer.querySelectorAll('.btn-toggle-key').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const prov = e.currentTarget.dataset.provider;
                const input = this.keysContainer.querySelector(`input[data-provider="${prov}"]`);
                if (input) {
                    input.type = input.type === 'password' ? 'text' : 'password';
                    e.currentTarget.innerHTML = input.type === 'password'
                        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
                        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
                }
            });
        });

        // Save buttons
        this.keysContainer.querySelectorAll('.btn-save-key').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const provider = e.target.dataset.provider;
                const input = this.keysContainer.querySelector(`input[data-provider="${provider}"]`);
                const key = input.value.trim();
                if (!key) return;

                const ok = await this._saveApiKey(provider, key);
                if (ok) {
                    input.value = '';
                    input.placeholder = 'Guardada ✓';
                    setTimeout(() => { input.placeholder = 'sk-... o clave API'; }, 2000);
                }
            });
        });

        this._checkApiKeyStatus();
    }

    async _saveApiKey(provider, key) {
        try {
            const resp = await fetch(`${BACKEND_API}/api-keys`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider, api_key: key }),
            });
            const data = await resp.json();
            if (resp.ok && data.success) {
                const statusEl = document.getElementById(`key-status-${provider}`);
                if (statusEl) {
                    statusEl.textContent = '✓ guardada';
                    statusEl.className = 'api-key-status set';
                }
                console.log(`API key guardada: ${provider}`);
                return true;
            } else {
                console.error(`Error del servidor guardando key ${provider}:`, data);
                const statusEl = document.getElementById(`key-status-${provider}`);
                if (statusEl) {
                    statusEl.textContent = '✗ error';
                    statusEl.className = 'api-key-status error';
                }
                return false;
            }
        } catch (err) {
            console.error(`Error de red guardando key ${provider}:`, err);
            const statusEl = document.getElementById(`key-status-${provider}`);
            if (statusEl) {
                statusEl.textContent = '✗ sin conexión';
                statusEl.className = 'api-key-status error';
            }
            return false;
        }
    }

    _togglePasswordInput(input, button) {
        if (!input || !button) return;
        input.type = input.type === 'password' ? 'text' : 'password';
        button.textContent = input.type === 'password' ? 'Ver' : 'Ocultar';
    }

    _cloneVoiceDebug(value) {
        if (value === undefined) return undefined;
        try {
            return JSON.parse(JSON.stringify(value));
        } catch (err) {
            return String(value);
        }
    }

    _voiceDebug(event, details = {}) {
        console.log(`[Settings][Voice] ${event}`, this._cloneVoiceDebug(details));
    }

    _normalizeVoiceDraft(source = {}) {
        const fallbackSettings = this.voiceMetadata?.settings || {};
        const normalizedSpeed = Number.parseFloat(
            String(source.tts_speed ?? fallbackSettings.tts_speed ?? '1.0'),
        );
        return {
            tts_primary: String(source.tts_primary ?? fallbackSettings.tts_primary ?? 'melotts') || 'melotts',
            tts_speed: Number.isFinite(normalizedSpeed) ? normalizedSpeed : 1.0,
            google_voice: String(source.google_voice ?? fallbackSettings.google_voice ?? 'Kore') || 'Kore',
            elevenlabs_voice_id: String(
                source.elevenlabs_voice_id ?? fallbackSettings.elevenlabs_voice_id ?? '',
            ),
            auto_tts: Boolean(source.auto_tts ?? fallbackSettings.auto_tts ?? false),
            enabled: source.enabled === undefined
                ? fallbackSettings.enabled !== false
                : source.enabled !== false,
        };
    }

    _buildVoiceDraftFromMetadata(data = this.voiceMetadata) {
        return this._normalizeVoiceDraft(data?.settings || {});
    }

    _readVoiceDraftFromControls() {
        const fallback = this._buildVoiceDraftFromMetadata();
        const sliderValue = Number.parseFloat(String(this.voiceSpeedSlider?.value ?? fallback.tts_speed));
        return this._normalizeVoiceDraft({
            tts_primary: this.voiceTtsSelect?.value || this.voiceDraft?.tts_primary || fallback.tts_primary,
            tts_speed: Number.isFinite(sliderValue) ? sliderValue : fallback.tts_speed,
            google_voice: this.voiceGoogleVoiceSelect?.value || this.voiceDraft?.google_voice || fallback.google_voice,
            elevenlabs_voice_id: this.voiceElevenlabsVoiceIdInput?.value ?? this.voiceDraft?.elevenlabs_voice_id ?? fallback.elevenlabs_voice_id,
            auto_tts: document.getElementById('cb-auto-tts')?.checked ?? this.voiceDraft?.auto_tts ?? fallback.auto_tts,
            enabled: document.getElementById('cb-voice-enabled')?.checked ?? this.voiceDraft?.enabled ?? fallback.enabled,
        });
    }

    _getEffectiveVoiceDraft() {
        if (this.voiceDraft) return this._normalizeVoiceDraft(this.voiceDraft);
        return this._readVoiceDraftFromControls();
    }

    _getSelectedVoiceEngine() {
        return this._getEffectiveVoiceDraft().tts_primary || 'melotts';
    }

    _hasVoicePendingChanges() {
        const draft = this.voiceDraft;
        const persisted = this.voiceMetadata?.settings;
        if (!draft || !persisted) return false;
        const normalizedDraft = this._normalizeVoiceDraft(draft);
        const normalizedPersisted = this._normalizeVoiceDraft(persisted);
        return ['tts_primary', 'google_voice', 'elevenlabs_voice_id', 'auto_tts', 'enabled'].some(
            (key) => normalizedDraft[key] !== normalizedPersisted[key],
        ) || Math.abs(normalizedDraft.tts_speed - normalizedPersisted.tts_speed) > 0.0001;
    }

    _updateVoiceDraft(patch, reason = 'unknown') {
        const before = this._cloneVoiceDebug(this.voiceDraft || this._readVoiceDraftFromControls());
        const base = this.voiceDraft ? this._normalizeVoiceDraft(this.voiceDraft) : this._readVoiceDraftFromControls();
        this.voiceDraft = this._normalizeVoiceDraft({ ...base, ...patch });
        this.voiceDraftDirty = this._hasVoicePendingChanges();
        this._voiceDebug('draft:update', {
            reason,
            patch,
            before,
            after: this.voiceDraft,
            dirty: this.voiceDraftDirty,
            persisted: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
        });
        return this.voiceDraft;
    }

    _applyVoiceDraftToControls(reason = 'unknown') {
        const draft = this._getEffectiveVoiceDraft();
        if (this.voiceTtsSelect) {
            const hasOption = Array.from(this.voiceTtsSelect.options || []).some((option) => option.value === draft.tts_primary);
            if (hasOption) {
                this.voiceTtsSelect.value = draft.tts_primary;
            }
        }
        if (this.voiceElevenlabsVoiceIdInput) {
            this.voiceElevenlabsVoiceIdInput.value = draft.elevenlabs_voice_id || '';
        }
        if (this.voiceGoogleVoiceSelect) {
            const hasOption = Array.from(this.voiceGoogleVoiceSelect.options || []).some(
                (o) => o.value === draft.google_voice,
            );
            if (hasOption) this.voiceGoogleVoiceSelect.value = draft.google_voice;
        }
        if (this.voiceSpeedSlider) {
            this.voiceSpeedSlider.value = String(draft.tts_speed ?? 1.0);
        }
        if (this.voiceSpeedValue) {
            this.voiceSpeedValue.textContent = String(draft.tts_speed ?? 1.0);
        }
        const cbAuto = document.getElementById('cb-auto-tts');
        if (cbAuto) cbAuto.checked = !!draft.auto_tts;
        const cbEnabled = document.getElementById('cb-voice-enabled');
        if (cbEnabled) cbEnabled.checked = !!draft.enabled;
        this._voiceDebug('draft:apply-controls', {
            reason,
            draft,
            selectedDomValue: this.voiceTtsSelect?.value || null,
        });
    }

    _applyVoiceMetadataSnapshot(data, { preserveDraft = false, reason = 'unknown' } = {}) {
        const previousDraft = this._cloneVoiceDebug(this.voiceDraft);
        const persistedDraft = this._buildVoiceDraftFromMetadata(data);
        const controlSnapshot = this._readVoiceDraftFromControls();
        this.voiceMetadata = data || {};
        this.voiceDraft = preserveDraft
            ? this._normalizeVoiceDraft({ ...persistedDraft, ...(this.voiceDraft || controlSnapshot) })
            : persistedDraft;
        this.voiceDraftDirty = this._hasVoicePendingChanges();
        this._voiceDebug('metadata:apply', {
            reason,
            preserveDraft,
            previousDraft,
            controlSnapshot,
            persisted: persistedDraft,
            nextDraft: this.voiceDraft,
            runtime: this._cloneVoiceDebug(this.voiceMetadata?.runtime || null),
        });
        this._renderVoiceTtsOptions();
        this._applyVoiceDraftToControls(reason);
        this._renderVoiceEngineState();
    }

    async _saveVoiceApiKey(provider, inputEl) {
        const key = String(inputEl?.value || '').trim();
        if (!key) return;

        this._voiceDebug('api-key:save:start', {
            provider,
            selectedBeforeSave: this.voiceTtsSelect?.value || null,
            draftBeforeSave: this._cloneVoiceDebug(this._getEffectiveVoiceDraft()),
            persistedBeforeSave: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
            hasKey: !!key,
        });

        const ok = await this._saveApiKey(provider, key);
        if (!ok) return;

        inputEl.value = '';
        if (provider === 'google') {
            inputEl.placeholder = 'Guardada';
        } else if (provider === 'elevenlabs') {
            inputEl.placeholder = 'Guardada';
        }

        // Si el usuario tiene el motor TTS correspondiente seleccionado en el draft
        // pero aún no persistido, guardarlo automáticamente para que se active con la nueva key.
        const draftEngine = this.voiceDraft?.tts_primary || this.voiceTtsSelect?.value || '';
        const persistedEngine = this.voiceMetadata?.settings?.tts_primary || '';
        const isGoogleEngine = (e) => String(e || '').startsWith('gemini-');
        const shouldAutoSaveEngine = (
            (provider === 'google' && isGoogleEngine(draftEngine) && !isGoogleEngine(persistedEngine)) ||
            (provider === 'elevenlabs' && draftEngine === 'elevenlabs' && persistedEngine !== 'elevenlabs')
        );
        if (shouldAutoSaveEngine) {
            this._voiceDebug('api-key:save:auto-engine', { provider, draftEngine, persistedEngine });
            await this._saveConfigValue('voice', 'tts_primary', draftEngine);
        }

        await this._checkApiKeyStatus();
        await this._syncVoiceMetadata({ preserveDraft: true, reason: `api-key-save:${provider}` });
        this._voiceDebug('api-key:save:done', {
            provider,
            selectedAfterSave: this.voiceTtsSelect?.value || null,
            draftAfterSave: this._cloneVoiceDebug(this.voiceDraft),
            persistedAfterSave: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
            runtimeAfterSave: this._cloneVoiceDebug(this.voiceMetadata?.runtime || null),
        });
    }

    _renderVoiceTtsOptions() {
        if (!this.voiceTtsSelect) return;

        const engines = Array.isArray(this.voiceMetadata?.engines) ? this.voiceMetadata.engines : [];
        const selectedEngine = this._getSelectedVoiceEngine();
        this.voiceTtsSelect.innerHTML = '';

        if (engines.length === 0) {
            this.voiceTtsSelect.appendChild(new Option('Sin motores disponibles', 'none'));
            this.voiceTtsSelect.value = 'none';
            this._voiceDebug('render:tts-options:empty', {
                selectedEngine,
                metadata: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
            });
            return;
        }

        engines.forEach((engine) => {
            const option = new Option(engine.label || engine.id, engine.id);
            this.voiceTtsSelect.appendChild(option);
        });

        this.voiceTtsSelect.value = engines.some((engine) => engine.id === selectedEngine)
            ? selectedEngine
            : engines[0].id;

        // Poblar selector de voces Google desde el metadata
        if (this.voiceGoogleVoiceSelect && Array.isArray(this.voiceMetadata?.google_voices)) {
            const currentVal = this.voiceDraft?.google_voice || 'Kore';
            this.voiceGoogleVoiceSelect.innerHTML = '';
            this.voiceMetadata.google_voices.forEach(({ id, description }) => {
                const opt = new Option(`${id}${description ? ` — ${description}` : ''}`, id);
                this.voiceGoogleVoiceSelect.appendChild(opt);
            });
            this.voiceGoogleVoiceSelect.value = currentVal;
        }
        this._voiceDebug('render:tts-options', {
            selectedEngine,
            domValue: this.voiceTtsSelect.value,
            engineIds: engines.map((engine) => engine.id),
            draft: this._cloneVoiceDebug(this.voiceDraft),
            persisted: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
        });
    }

    _setInlineKeyStatus(element, info) {
        if (!element) return;
        if (info && info.configured) {
            element.textContent = `OK ${info.masked || ''}`.trim();
            element.className = 'api-key-status set';
        } else {
            element.textContent = 'sin clave';
            element.className = 'api-key-status unset';
        }
    }

    _buildVoiceRuntimeText() {
        const runtime = this.voiceMetadata?.runtime || {};
        const engineList = Array.isArray(this.voiceMetadata?.engines) ? this.voiceMetadata.engines : [];
        const selectedEngine = this._getSelectedVoiceEngine() || runtime.requested_engine || '';
        const selectedMeta = engineList.find((engine) => engine.id === selectedEngine) || {};
        const selectedLabel = selectedMeta.label || runtime.requested_label || 'Sin seleccionar';
        const activeLabel = runtime.available
            ? (runtime.active_label || runtime.active_engine || 'Activo')
            : 'bloqueado';
        const message = runtime.message ? ` ${runtime.message}` : '';
        const warnings = Array.isArray(runtime.warnings) && runtime.warnings.length > 0
            ? ` Avisos: ${runtime.warnings.join(' ')}`
            : '';
        const pendingSave = this.voiceDraftDirty
            ? ' Cambio pendiente de guardar.'
            : '';

        return runtime.available
            ? `Seleccionado: ${selectedLabel}. Activo: ${activeLabel}.${pendingSave}${warnings}`.trim()
            : `Seleccionado: ${selectedLabel}. Activo: ${activeLabel}.${message}${pendingSave}${warnings}`.trim();
    }

    _populateWebspeechVoices() {
        const sel = this.voiceWebspeechVoiceSelect;
        if (!sel || typeof window.speechSynthesis === 'undefined') return;
        const voices = window.speechSynthesis.getVoices() || [];
        const saved = (() => { try { return localStorage.getItem('webspeech_voice') || ''; } catch (_) { return ''; } })();
        sel.innerHTML = '';
        sel.appendChild(new Option('Voz por defecto del sistema', ''));
        // Español primero, resto después.
        voices
            .slice()
            .sort((a, b) => {
                const sa = (a.lang || '').toLowerCase().startsWith('es') ? 0 : 1;
                const sb = (b.lang || '').toLowerCase().startsWith('es') ? 0 : 1;
                return sa - sb || (a.name || '').localeCompare(b.name || '');
            })
            .forEach((v) => sel.appendChild(new Option(`${v.name} (${v.lang})`, v.name)));
        sel.value = voices.some((v) => v.name === saved) ? saved : '';
    }

    _renderVoiceEngineState() {
        const selectedEngine = this._getSelectedVoiceEngine();
        const engineList = Array.isArray(this.voiceMetadata?.engines) ? this.voiceMetadata.engines : [];
        const selectedMeta = engineList.find((engine) => engine.id === selectedEngine) || {};
        const provider = selectedMeta.provider || 'unknown';
        const supportsNumericSpeed = selectedMeta.supports_numeric_speed !== false;

        if (this.voiceGoogleConfig) {
            this.voiceGoogleConfig.classList.toggle('hidden', provider !== 'google');
        }
        if (this.voiceGoogleVoiceConfig) {
            this.voiceGoogleVoiceConfig.classList.toggle('hidden', provider !== 'google');
        }
        if (this.voiceElevenlabsConfig) {
            this.voiceElevenlabsConfig.classList.toggle('hidden', selectedEngine !== 'elevenlabs');
        }
        if (this.voiceWebspeechConfig) {
            const isBrowser = provider === 'browser';
            this.voiceWebspeechConfig.classList.toggle('hidden', !isBrowser);
            if (isBrowser) this._populateWebspeechVoices();
        }

        // El slider de velocidad solo aplica a motores que lo soportan
        const speedGroup = this.voiceSpeedSlider?.closest('.setting-group');
        if (speedGroup) {
            speedGroup.classList.toggle('hidden', !supportsNumericSpeed);
        }
        if (this.voiceSpeedSlider) {
            this.voiceSpeedSlider.disabled = !supportsNumericSpeed;
        }
        if (this.voiceSpeedHelp) {
            this.voiceSpeedHelp.classList.add('hidden');
            this.voiceSpeedHelp.textContent = '';
        }

        this.voiceDraftDirty = this._hasVoicePendingChanges();
        if (this.voiceRuntimeStatus) {
            this.voiceRuntimeStatus.textContent = this._buildVoiceRuntimeText();
        }
        this._setInlineKeyStatus(this.voiceGoogleKeyStatus, this.voiceApiKeyStatus.google);
        this._setInlineKeyStatus(this.voiceElevenlabsKeyStatus, this.voiceApiKeyStatus.elevenlabs);
        this._voiceDebug('render:engine-state', {
            selectedEngine,
            provider,
            supportsNumericSpeed,
            draft: this._cloneVoiceDebug(this.voiceDraft),
            persisted: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
            runtime: this._cloneVoiceDebug(this.voiceMetadata?.runtime || null),
            dirty: this.voiceDraftDirty,
        });
    }

    async _syncVoiceMetadata({ preserveDraft = false, reason = 'sync' } = {}) {
        this._voiceDebug('metadata:sync:start', {
            reason,
            preserveDraft,
            currentDraft: this._cloneVoiceDebug(this.voiceDraft),
            currentPersisted: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
        });
        try {
            const resp = await fetch(`${BACKEND_API}/voice/metadata`);
            if (!resp.ok) return;
            const payload = await resp.json();
            const data = payload?.data || {};
            this._voiceDebug('metadata:sync:response', {
                reason,
                preserveDraft,
                metadata: this._cloneVoiceDebug(data),
            });
            this._applyVoiceMetadataSnapshot(data, { preserveDraft, reason });
        } catch (err) {
            console.error('[Settings][Voice] Error sincronizando voice metadata:', err);
        }
    }


    async _syncModesFromBackend() {
        try {
            const resp = await fetch(`${BACKEND_API}/modes`);
            if (!resp.ok) return;
            const data = await resp.json();
            if (Array.isArray(data.modes)) {
                this.availableModes = data.modes;
                this._renderModes();
            }
            if (data.current_mode) {
                this.currentMode = data.current_mode;
                if (this.modeSelect) this.modeSelect.value = this.currentMode;
                this.updateModeLabel(data.current_mode_name || this.currentMode);
                this._renderModeSummary(data);
                this._renderCustomModeEditor();
            }
        } catch (err) {
            // Backend no listo
        }
    }

    async _applyModeChange() {
        try {
            const resp = await fetch(`${BACKEND_API}/modes/${this.currentMode}`, {
                method: 'PUT',
            });
            if (!resp.ok) return;
            const data = await resp.json();
            this.currentMode = data.current_mode || this.currentMode;
            if (this.modeSelect) this.modeSelect.value = this.currentMode;
            this.updateModeLabel(data.current_mode_name || this.currentMode);
            this._renderModeSummary(data);
            this._renderCustomModeEditor();
        } catch (err) {
            console.error('Error cambiando modo:', err);
        }
    }

    async _checkApiKeyStatus() {
        try {
            const resp = await fetch(`${BACKEND_API}/api-keys/status`);
            if (!resp.ok) return;
            const data = await resp.json();
            this.voiceApiKeyStatus = data || {};
            for (const [provider, info] of Object.entries(data)) {
                const statusEl = document.getElementById(`key-status-${provider}`);
                if (!statusEl) continue;
                if (info && info.configured) {
                    statusEl.textContent = `✓ ${info.masked || 'configurada'}`;
                    statusEl.className = 'api-key-status set';
                } else {
                    statusEl.textContent = '✗ sin clave';
                    statusEl.className = 'api-key-status unset';
                }
            }
            this._renderVoiceEngineState();
        } catch (err) {
            // Backend no disponible aún
        }
    }

    async _loadModelsCatalog() {
        try {
            const resp = await fetch(`${BACKEND_API}/models/catalog`);
            if (!resp.ok) return;
            const catalog = await resp.json();

            // Actualizar variables globales desde el YAML
            if (catalog.provider_labels && Object.keys(catalog.provider_labels).length > 0) {
                PROVIDER_LABELS = catalog.provider_labels;
            }
            if (catalog.llm && Object.keys(catalog.llm).length > 0) {
                MODEL_OPTIONS = catalog.llm;
            }
            if (Array.isArray(catalog.image) && catalog.image.length > 0) {
                GOOGLE_IMAGE_MODELS = catalog.image;
            }
            if (Array.isArray(catalog.video) && catalog.video.length > 0) {
                GOOGLE_VIDEO_MODELS = catalog.video;
            }
            if (Array.isArray(catalog.music) && catalog.music.length > 0) {
                GOOGLE_MUSIC_MODELS = catalog.music;
            }

            // Re-renderizar dropdowns con los datos cargados
            this._renderApiKeys();
            this._updateModelOptions();

            // Re-poblar selectores de modelos generativos
            const imageSelect = document.getElementById('select-image-model');
            const videoSelect = document.getElementById('select-video-model');
            const musicSelect = document.getElementById('select-music-model');
            if (imageSelect) {
                imageSelect.innerHTML = '';
                GOOGLE_IMAGE_MODELS.forEach(m => imageSelect.appendChild(new Option(m, m)));
            }
            if (videoSelect) {
                videoSelect.innerHTML = '';
                GOOGLE_VIDEO_MODELS.forEach(m => videoSelect.appendChild(new Option(m, m)));
            }
            if (musicSelect) {
                musicSelect.innerHTML = '';
                GOOGLE_MUSIC_MODELS.forEach(m => musicSelect.appendChild(new Option(m, m)));
            }

            console.log('[Settings] Catálogo de modelos cargado desde backend:', Object.keys(catalog.llm || {}).length, 'proveedores');
        } catch (err) {
            console.warn('[Settings] No se pudo cargar catálogo de modelos, usando valores vacíos:', err.message);
        }
    }

    async _syncFromBackend() {
        // Sincronizar modelos generativos
        try {
            const respGenerative = await fetch(`${BACKEND_API}/config/generative_models`);
            if (respGenerative.ok) {
                const dataGen = await respGenerative.json();
                const genModels = dataGen?.data?.generative_models || {};
                const imgSel = document.getElementById('select-image-model');
                const vidSel = document.getElementById('select-video-model');
                const musSel = document.getElementById('select-music-model');
                
                if (imgSel && genModels.image_model) imgSel.value = genModels.image_model;
                if (vidSel && genModels.video_model) vidSel.value = genModels.video_model;
                if (musSel && genModels.music_model) musSel.value = genModels.music_model;
            }
        } catch (err) {
            // Se ignora si aún no está configurado
        }

        try {
            const resp = await fetch(`${BACKEND_API}/config/app`);
            if (resp.ok) {
                const data = await resp.json();
                const appConfig = data?.data?.app || {};
                this.currentStartWithWindows = !!appConfig.start_with_windows;
                this.currentMinimizeToTray = appConfig.minimize_to_tray !== false;
                this.currentCloseToTray = appConfig.close_to_tray !== false;
                this.currentStartHiddenToTray = !!appConfig.start_hidden_to_tray;
                if (this.appStartWithWindowsCheckbox) {
                    this.appStartWithWindowsCheckbox.checked = this.currentStartWithWindows;
                }
                if (this.appMinimizeToTrayCheckbox) {
                    this.appMinimizeToTrayCheckbox.checked = this.currentMinimizeToTray;
                }
                if (this.appCloseToTrayCheckbox) {
                    this.appCloseToTrayCheckbox.checked = this.currentCloseToTray;
                }
                if (this.appStartHiddenToTrayCheckbox) {
                    this.appStartHiddenToTrayCheckbox.checked = this.currentStartHiddenToTray;
                }
            }
        } catch (err) {
            // Backend no listo
        }
        await this._syncAppRuntimeSettings();
        this._renderAppBehaviorMeta();
        try {
            const resp = await fetch(`${BACKEND_API}/config/model_router`);
            if (!resp.ok) return;
            const data = await resp.json();
            const mr = data?.data?.model_router;
            if (mr) {
                if (mr.default_provider) {
                    this.currentProvider = mr.default_provider;
                    this.providerSelect.value = this.currentProvider;
                }
                if (mr.default_model) {
                    this.currentModel = mr.default_model;
                }
                this._updateModelOptions();
                this.updateModelLabel();
                // Consultar disponibilidad RT con el provider y modelo reales
                ws.checkRealtimeAvailable(this.currentProvider, this.currentModel);
            }
        } catch (err) {
            // Backend no listo
        }
        await this._syncGoogleBackendConfig();
        await this._syncMonitors();
        try {
            const resp = await fetch(`${BACKEND_API}/config/agent`);
            if (resp.ok) {
                const data = await resp.json();
                const agentConfig = data?.data?.agent || {};
                this.currentAutonomy = agentConfig.autonomy || 'media';
                if (this.autonomySelect) {
                    this.autonomySelect.value = this.currentAutonomy;
                }
                this.currentAutonomyLevel = agentConfig.autonomy_level || 'supervisado';
                if (this.autonomyLevelSelect) {
                    this.autonomyLevelSelect.value = this.currentAutonomyLevel;
                }
                if (this.blockedSitesEnabledCheckbox) {
                    this.blockedSitesEnabledCheckbox.checked = !!agentConfig.blocked_sites_enabled;
                }
                if (this.blockedSitesInput) {
                    this.blockedSitesInput.value = Array.isArray(agentConfig.blocked_sites)
                        ? agentConfig.blocked_sites.join('\n')
                        : '';
                }
                this._renderAutonomyScopeMeta();
                this._renderAutonomyMeta();
                this._renderBlockedSitesMeta();
            }
        } catch (err) {
            // Backend no listo
        }
        try {
            const resp = await fetch(`${BACKEND_API}/config/terminals`);
            if (resp.ok) {
                const data = await resp.json();
                const terminalConfig = data?.data?.terminals || {};
                this.execApprovalsProfiles = terminalConfig.exec_approvals_by_host || {};
                if (this.execApprovalsEnabledCheckbox) {
                    this.execApprovalsEnabledCheckbox.checked = !!terminalConfig.exec_approvals_enabled;
                }
                if (terminalConfig.exec_approval_host_key) {
                    this.currentExecApprovalHostKey = this._normalizeHostKey(terminalConfig.exec_approval_host_key);
                }
            }
        } catch (err) {
            // Backend no listo
        }
        try {
            const resp = await fetch(`${BACKEND_API}/terminals`);
            if (resp.ok) {
                const data = await resp.json();
                const execState = data?.exec_approvals || {};
                const hostKey = this._normalizeHostKey(
                    execState.host_key || this.currentExecApprovalHostKey || 'default-host'
                );
                this.currentExecApprovalHostKey = hostKey;
                if (this.execApprovalsHostInput) {
                    this.execApprovalsHostInput.value = hostKey;
                }
                const profile = this.execApprovalsProfiles[hostKey] || {
                    allowed_commands: execState.allowed_commands || [],
                    allowed_patterns: execState.allowed_patterns || [],
                    denied_patterns: execState.denied_patterns || [],
                };
                if (this.execAllowedCommandsInput) {
                    this.execAllowedCommandsInput.value = Array.isArray(profile.allowed_commands)
                        ? profile.allowed_commands.join('\n')
                        : '';
                }
                if (this.execAllowedPatternsInput) {
                    this.execAllowedPatternsInput.value = Array.isArray(profile.allowed_patterns)
                        ? profile.allowed_patterns.join('\n')
                        : '';
                }
                if (this.execDeniedPatternsInput) {
                    this.execDeniedPatternsInput.value = Array.isArray(profile.denied_patterns)
                        ? profile.denied_patterns.join('\n')
                        : '';
                }
                this._renderExecApprovalsMeta();
            }
        } catch (err) {
            // Backend no listo
        }
        try {
            const resp = await fetch(`${BACKEND_API}/config/mcp`);
            if (resp.ok) {
                const data = await resp.json();
                const mcpConfig = data?.data?.mcp || {};
                this.currentMcpServers = Array.isArray(mcpConfig.servers) ? mcpConfig.servers : [];
                if (this.mcpEnabledCheckbox) {
                    this.mcpEnabledCheckbox.checked = !!mcpConfig.enabled;
                }
                if (this.mcpIntegrationModeSelect) {
                    this.mcpIntegrationModeSelect.value = mcpConfig.integration_mode || 'preloaded';
                }
                this._renderMcpServers();
            }
        } catch (err) {
            // Backend no listo
        }
        try {
            const [gatewayConfigResp, gatewayCredResp, gatewayStatusResp, gatewayWhatsAppRuntimeResp, gatewayDiscordRuntimeResp] = await Promise.all([
                fetch(`${BACKEND_API}/config/gateway`),
                fetch(`${BACKEND_API}/gateway/credentials/status`),
                fetch(`${BACKEND_API}/gateway/status`),
                fetch(`${BACKEND_API}/gateway/runtime/whatsapp`),
                fetch(`${BACKEND_API}/gateway/runtime/discord`),
            ]);
            if (gatewayConfigResp.ok) {
                const gatewayConfigData = await gatewayConfigResp.json();
                const gatewayConfig = gatewayConfigData?.data?.gateway || {};
                this.currentGatewayChannels = gatewayConfig.channels && typeof gatewayConfig.channels === 'object'
                    ? gatewayConfig.channels
                    : {};
                const telegramConfig = this.currentGatewayChannels.telegram || {};
                const whatsappConfig = this.currentGatewayChannels.whatsapp || {};
                const discordConfig = this.currentGatewayChannels.discord || {};
                if (this.gatewayTelegramEnabledCheckbox) {
                    this.gatewayTelegramEnabledCheckbox.checked = telegramConfig.enabled === true;
                }
                if (this.gatewayTelegramDefaultChatInput) {
                    this.gatewayTelegramDefaultChatInput.value = String(telegramConfig.default_chat_id || '');
                }
                if (this.gatewayTelegramAllowedChatsInput) {
                    const allowedChats = Array.isArray(telegramConfig.allowed_chat_ids) ? telegramConfig.allowed_chat_ids : [];
                    this.gatewayTelegramAllowedChatsInput.value = allowedChats.join('\n');
                }
                if (this.gatewayWhatsAppEnabledCheckbox) {
                    this.gatewayWhatsAppEnabledCheckbox.checked = whatsappConfig.enabled === true;
                }
                if (this.gatewayWhatsAppDefaultChatInput) {
                    this.gatewayWhatsAppDefaultChatInput.value = String(whatsappConfig.default_chat_id || '');
                }
                if (this.gatewayWhatsAppAllowedChatsInput) {
                    const allowedWhatsAppChats = Array.isArray(whatsappConfig.allowed_chat_ids)
                        ? whatsappConfig.allowed_chat_ids
                        : [];
                    this.gatewayWhatsAppAllowedChatsInput.value = allowedWhatsAppChats.join('\n');
                }
                if (this.gatewayWhatsAppSessionNameInput) {
                    this.gatewayWhatsAppSessionNameInput.value = String(whatsappConfig.session_name || 'default');
                }
                if (this.gatewayDiscordEnabledCheckbox) {
                    this.gatewayDiscordEnabledCheckbox.checked = discordConfig.enabled === true;
                }
                if (this.gatewayDiscordDefaultChannelInput) {
                    this.gatewayDiscordDefaultChannelInput.value = String(discordConfig.default_channel_id || '');
                }
                if (this.gatewayDiscordAllowedGuildsInput) {
                    const allowedGuilds = Array.isArray(discordConfig.allowed_guild_ids) ? discordConfig.allowed_guild_ids : [];
                    this.gatewayDiscordAllowedGuildsInput.value = allowedGuilds.join('\n');
                }
                if (this.gatewayDiscordAllowedChannelsInput) {
                    const allowedChannels = Array.isArray(discordConfig.allowed_channel_ids) ? discordConfig.allowed_channel_ids : [];
                    this.gatewayDiscordAllowedChannelsInput.value = allowedChannels.join('\n');
                }
            }
            if (gatewayCredResp.ok) {
                const gatewayCredData = await gatewayCredResp.json();
                const credentials = Array.isArray(gatewayCredData?.credentials) ? gatewayCredData.credentials : [];
                this.currentGatewayTelegramCredential = credentials.find((item) => item.channel === 'telegram') || { configured: false, masked: null };
                this.currentGatewayDiscordCredential = credentials.find((item) => item.channel === 'discord') || { configured: false, masked: null };
            }
            if (gatewayStatusResp.ok) {
                this.gatewayRuntimeStatus = await gatewayStatusResp.json();
            }
            if (gatewayWhatsAppRuntimeResp.ok) {
                const whatsappRuntimeData = await gatewayWhatsAppRuntimeResp.json();
                this.currentGatewayWhatsAppRuntime = whatsappRuntimeData?.data && typeof whatsappRuntimeData.data === 'object'
                    ? whatsappRuntimeData.data
                    : {};
            }
            if (gatewayDiscordRuntimeResp.ok) {
                const discordRuntimeData = await gatewayDiscordRuntimeResp.json();
                this.currentGatewayDiscordRuntime = discordRuntimeData?.data && typeof discordRuntimeData.data === 'object'
                    ? discordRuntimeData.data
                    : {};
            }
            this._renderGatewayTelegramMeta();
            this._renderGatewayWhatsAppMeta();
            this._renderGatewayDiscordMeta();
        } catch (err) {
            // Backend no listo
        }
        try {
            const resp = await fetch(`${BACKEND_API}/config/skills`);
            if (resp.ok) {
                const data = await resp.json();
                const skillsConfig = data?.data?.skills || {};
                this.currentSkillsPreferred = Array.isArray(skillsConfig.preferred) ? skillsConfig.preferred : [];
                this.currentSkillsPaths = Array.isArray(skillsConfig.custom_paths) ? skillsConfig.custom_paths : [];
                if (this.skillsEnabledCheckbox) {
                    this.skillsEnabledCheckbox.checked = !!skillsConfig.enabled;
                }
                if (this.skillsPreferredInput) {
                    this.skillsPreferredInput.value = this.currentSkillsPreferred.join('\n');
                }
                if (this.skillsPathsInput) {
                    this.skillsPathsInput.value = this.currentSkillsPaths.join('\n');
                }
                this._renderSkillsMeta();
            }
        } catch (err) {
            // Backend no listo
        }
        try {
            const resp = await fetch(`${BACKEND_API}/config/scheduler`);
            if (resp.ok) {
                const data = await resp.json();
                const schedulerConfig = data?.data?.scheduler || {};
                this.currentSchedulerEnabled = schedulerConfig.enabled !== false;
                this.currentSchedulerPollInterval = Number(schedulerConfig.poll_interval_seconds || 2.0);
                if (this.schedulerEnabledCheckbox) {
                    this.schedulerEnabledCheckbox.checked = this.currentSchedulerEnabled;
                }
                if (this.schedulerPollIntervalInput) {
                    this.schedulerPollIntervalInput.value = String(this.currentSchedulerPollInterval);
                }
                this._renderSchedulerSettingsMeta();
            }
        } catch (err) {
            // Backend no listo
        }
        try {
            const [budgetResp, modelRouterResp, paymentsResp] = await Promise.all([
                fetch(`${BACKEND_API}/config/budget`),
                fetch(`${BACKEND_API}/config/model_router`),
                fetch(`${BACKEND_API}/config/payments`),
            ]);
            if (budgetResp.ok) {
                const data = await budgetResp.json();
                const budgetConfig = data?.data?.budget || {};
                this.currentBudgetEnabled = budgetConfig.enabled !== false;
                this.currentBudgetDailyLimit = Number(budgetConfig.daily_limit_usd || 0);
                this.currentBudgetMonthlyLimit = Number(budgetConfig.monthly_limit_usd || 0);
                this.currentBudgetWarningThreshold = Number(budgetConfig.warning_threshold_percent || 80);
                this.currentBudgetSubagentLimit = Number(budgetConfig.subagent_default_task_limit_usd || 0);
                this.currentBudgetSubagentShare = Number(budgetConfig.subagent_parent_budget_share || 0);
                this.currentBudgetModeLimits = budgetConfig.mode_task_limits_usd && typeof budgetConfig.mode_task_limits_usd === 'object'
                    ? budgetConfig.mode_task_limits_usd
                    : {};
                if (this.budgetEnabledCheckbox) {
                    this.budgetEnabledCheckbox.checked = this.currentBudgetEnabled;
                }
                if (this.budgetDailyLimitInput) {
                    this.budgetDailyLimitInput.value = String(this.currentBudgetDailyLimit || 0);
                }
                if (this.budgetMonthlyLimitInput) {
                    this.budgetMonthlyLimitInput.value = String(this.currentBudgetMonthlyLimit || 0);
                }
                if (this.budgetWarningThresholdInput) {
                    this.budgetWarningThresholdInput.value = String(this.currentBudgetWarningThreshold || 80);
                }
                if (this.budgetSubagentLimitInput) {
                    this.budgetSubagentLimitInput.value = String(this.currentBudgetSubagentLimit || 0);
                }
                if (this.budgetSubagentShareInput) {
                    this.budgetSubagentShareInput.value = String(this.currentBudgetSubagentShare || 0);
                }
                if (this.budgetModeLimitsInput) {
                    this.budgetModeLimitsInput.value = JSON.stringify(this.currentBudgetModeLimits, null, 2);
                }
            }
            if (modelRouterResp.ok) {
                const modelRouterData = await modelRouterResp.json();
                const hardLimits = modelRouterData?.data?.model_router?.hard_limits || {};
                this.currentModelRouterHardLimits = { ...hardLimits };
                this.currentTaskBudgetLimit = Number(hardLimits.max_cost_per_task_usd || 0);
                if (this.budgetTaskLimitInput) {
                    this.budgetTaskLimitInput.value = String(this.currentTaskBudgetLimit || 0);
                }
            }
            if (paymentsResp.ok) {
                const paymentsData = await paymentsResp.json();
                const paymentsConfig = paymentsData?.data?.payments || {};
                this.currentPaymentsEnabled = paymentsConfig.enabled !== false;
                this.currentSpendPermissionsMode = paymentsConfig.spend_permissions_mode || 'ask_always';
                this.currentSpendAskAboveUsd = Number(paymentsConfig.ask_above_usd || 0);
                this.currentSpendAutoApproveUnderUsd = Number(paymentsConfig.auto_approve_under_usd || 0);
                this.currentDefaultPaymentAccountId = String(paymentsConfig.default_account_id || '').trim();
                this.currentPaymentAccounts = Array.isArray(paymentsConfig.accounts) ? paymentsConfig.accounts : [];
                if (this.paymentsEnabledCheckbox) {
                    this.paymentsEnabledCheckbox.checked = this.currentPaymentsEnabled;
                }
                if (this.spendPermissionsModeSelect) {
                    this.spendPermissionsModeSelect.value = this.currentSpendPermissionsMode;
                }
                if (this.spendAskAboveInput) {
                    this.spendAskAboveInput.value = String(this.currentSpendAskAboveUsd || 0);
                }
                if (this.spendAutoApproveUnderInput) {
                    this.spendAutoApproveUnderInput.value = String(this.currentSpendAutoApproveUnderUsd || 0);
                }
                if (this.paymentsDefaultAccountInput) {
                    this.paymentsDefaultAccountInput.value = this.currentDefaultPaymentAccountId;
                }
                if (this.paymentsAccountsInput) {
                    this.paymentsAccountsInput.value = JSON.stringify(this.currentPaymentAccounts, null, 2);
                }
            }
            this._renderBudgetMeta();
            this._renderPaymentsMeta();
            this._renderPaymentAccountsMeta();
        } catch (err) {
            // Backend no listo
        }
        try {
            const [voiceMetaResp, charResp] = await Promise.all([
                fetch(`${BACKEND_API}/voice/metadata`),
                fetch(`${BACKEND_API}/config/character`),
            ]);
            if (voiceMetaResp.ok) {
                const voiceData = (await voiceMetaResp.json())?.data || {};
                this._applyVoiceMetadataSnapshot(voiceData, {
                    preserveDraft: false,
                    reason: 'syncFromBackend:initial-fetch',
                });
            }
            if (charResp.ok) {
                const cd = (await charResp.json())?.data?.character || {};
                const selChar = document.getElementById('select-character-type');
                let charType = String(cd.type || '3d');
                if (cd.skin === 'energy-ball' && charType === '2d') charType = '3d';
                if (!['3d', '2d', 'none'].includes(charType)) charType = '3d';
                if (selChar) selChar.value = charType;
                const selMode = document.getElementById('select-display-mode');
                if (selMode) selMode.value = cd.mode === 'skin' ? 'skin' : 'chat';
                await this._populateAvatarSkinOptions(cd.skin || 'energy-ball', charType);
            }
        } catch (err) {
            // Backend no listo
        }
        await this._syncMcpRuntime();
        await this._syncSkillsCatalog();
        // Also refresh mode and key status
        this._syncModesFromBackend();
        this._syncPromptTemplates();
        this._checkApiKeyStatus();
        await this._syncVoiceMetadata({ preserveDraft: false, reason: 'syncFromBackend:final-refresh' });
        // Restore sidebar page selection
        if (this.currentPage) this._switchPage(this.currentPage);
        // Sync model assignments + crews
        await this._syncModelAssignments();
        await this._loadComputerUseConfig();
        await this._syncCrews();
        this._toggleBudgetFields();
    }

    async _syncMcpRuntime() {
        try {
            const resp = await fetch(`${BACKEND_API}/mcp/servers`);
            if (!resp.ok) return;
            const data = await resp.json();
            this.mcpRuntimeServers = Array.isArray(data?.servers) ? data.servers : [];
            this._renderMcpServers();
        } catch (err) {
            // Backend no listo
        }
    }

    async _syncSkillsCatalog() {
        try {
            const resp = await fetch(`${BACKEND_API}/skills/catalog`);
            if (!resp.ok) return;
            const data = await resp.json();
            this.discoveredSkills = Array.isArray(data?.skills) ? data.skills : [];
            this.skillRoots = Array.isArray(data?.roots) ? data.roots : [];
            this._renderSkillsMeta();
        } catch (err) {
            // Backend no listo
        }
    }

    _parseMultilineList(rawValue) {
        return String(rawValue || '')
            .split(/\r?\n/)
            .map((item) => item.trim())
            .filter(Boolean);
    }

    _normalizeHostKey(rawValue) {
        const normalized = String(rawValue || '')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '');
        return normalized || 'default-host';
    }

    _parseJsonConfig(rawValue, fallbackValue) {
        const trimmed = String(rawValue || '').trim();
        if (!trimmed) return fallbackValue;
        return JSON.parse(trimmed);
    }

    async _saveExecApprovals() {
        const hostKey = this._normalizeHostKey(
            this.execApprovalsHostInput?.value || this.currentExecApprovalHostKey || 'default-host'
        );
        const profiles = { ...(this.execApprovalsProfiles || {}) };
        profiles[hostKey] = {
            allowed_commands: this._parseMultilineList(this.execAllowedCommandsInput?.value || ''),
            allowed_patterns: this._parseMultilineList(this.execAllowedPatternsInput?.value || ''),
            denied_patterns: this._parseMultilineList(this.execDeniedPatternsInput?.value || ''),
        };
        const enabled = !!this.execApprovalsEnabledCheckbox?.checked;

        await this._saveConfigValue('terminals', 'exec_approval_host_key', hostKey);
        await this._saveConfigValue('terminals', 'exec_approvals_by_host', profiles);
        await this._saveConfigValue('terminals', 'exec_approvals_enabled', enabled);

        this.currentExecApprovalHostKey = hostKey;
        this.execApprovalsProfiles = profiles;
        if (this.execApprovalsHostInput) {
            this.execApprovalsHostInput.value = hostKey;
        }
        this._renderExecApprovalsMeta();
    }

    async _saveMcpConfig() {
        await this._saveConfigValue('mcp', 'enabled', !!this.mcpEnabledCheckbox?.checked);
        await this._saveConfigValue('mcp', 'servers', this.currentMcpServers);
        await this._syncMcpRuntime();
    }

    async _addMcpServer() {
        const nameInput = document.getElementById('input-mcp-server-name');
        const name = (nameInput?.value || '').trim();
        if (!name) {
            if (this.mcpAddMeta) this.mcpAddMeta.textContent = 'El nombre del servidor es obligatorio.';
            return;
        }
        // Check duplicate
        if (this.currentMcpServers.some((s) => s.name === name || s.id === name)) {
            if (this.mcpAddMeta) this.mcpAddMeta.textContent = `Ya existe un servidor con el nombre "${name}".`;
            return;
        }
        const transport = this.mcpTransportSelect?.value || 'stdio';
        const server = { id: name, name, transport, enabled: true };
        if (transport === 'stdio') {
            const cmd = (document.getElementById('input-mcp-command')?.value || '').trim();
            if (!cmd) {
                if (this.mcpAddMeta) this.mcpAddMeta.textContent = 'El comando es obligatorio para servidores locales.';
                return;
            }
            server.command = cmd;
            const argsRaw = (document.getElementById('input-mcp-args')?.value || '').trim();
            server.args = argsRaw
                ? argsRaw.split(/[,\s]+/).map((a) => a.replace(/^["'\[\]]+|["'\[\]]+$/g, '').trim()).filter(Boolean)
                : [];
            const cwd = (document.getElementById('input-mcp-cwd')?.value || '').trim();
            if (cwd) server.cwd = cwd;
        } else {
            const url = (document.getElementById('input-mcp-url')?.value || '').trim();
            if (!url) {
                if (this.mcpAddMeta) this.mcpAddMeta.textContent = 'La URL es obligatoria para servidores remotos.';
                return;
            }
            server.url = url;
        }
        // Parse env
        const envRaw = (document.getElementById('input-mcp-env')?.value || '').trim();
        if (envRaw) {
            try {
                const envObj = JSON.parse(envRaw);
                if (typeof envObj === 'object' && !Array.isArray(envObj)) {
                    server.env = envObj;
                }
            } catch (err) {
                if (this.mcpAddMeta) this.mcpAddMeta.textContent = `JSON invalido en variables de entorno: ${err.message}`;
                return;
            }
        }
        this.currentMcpServers.push(server);
        await this._saveMcpConfig();
        // Clear form
        if (nameInput) nameInput.value = '';
        ['input-mcp-command', 'input-mcp-args', 'input-mcp-cwd', 'input-mcp-url', 'input-mcp-env'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        if (this.mcpAddMeta) this.mcpAddMeta.textContent = `Servidor "${name}" agregado correctamente.`;
        // Switch to configure tab
        this.panel.querySelectorAll('.mcp-tab').forEach((t) =>
            t.classList.toggle('active', t.dataset.mcpTab === 'configure')
        );
        this.panel.querySelectorAll('.mcp-tab-content').forEach((c) =>
            c.classList.toggle('active', c.dataset.mcpTab === 'configure')
        );
    }

    async _toggleMcpServer(serverId, enabled) {
        const server = this.currentMcpServers.find((s) => s.id === serverId || s.name === serverId);
        if (server) {
            server.enabled = enabled;
            await this._saveMcpConfig();
        }
    }

    async _removeMcpServer(serverId) {
        this.currentMcpServers = this.currentMcpServers.filter((s) => s.id !== serverId && s.name !== serverId);
        await this._saveMcpConfig();
    }

    async _refreshMcpServer(serverId) {
        // Fetch tools for this server to validate it
        try {
            const resp = await fetch(`${BACKEND_API}/mcp/servers/${encodeURIComponent(serverId)}/tools`);
            if (resp.ok) {
                const data = await resp.json();
                // Cache tools for this server
                if (!this._mcpToolsCache) this._mcpToolsCache = {};
                this._mcpToolsCache[serverId] = data.tools || [];
            }
        } catch (err) {
            // ignore
        }
        await this._syncMcpRuntime();
    }

    _renderMcpServers() {
        if (!this.mcpServersList) return;
        const runtimeMap = {};
        (this.mcpRuntimeServers || []).forEach((s) => { runtimeMap[s.id] = s; });

        this.mcpServersList.innerHTML = '';
        if (this.currentMcpServers.length === 0) return;

        for (const server of this.currentMcpServers) {
            const rt = runtimeMap[server.id || server.name] || {};
            const isReady = !!rt.ready;
            const statusClass = !server.enabled ? 'disabled' : (isReady ? 'ready' : 'error');
            const statusDetail = rt.detail || rt.status || (server.enabled ? 'pending' : 'disabled');

            const card = document.createElement('div');
            card.className = 'mcp-server-card';
            card.dataset.serverId = server.id || server.name;

            // ── Row ──
            const row = document.createElement('div');
            row.className = 'mcp-server-row';

            const expand = document.createElement('span');
            expand.className = 'mcp-server-expand';
            expand.textContent = '▶';

            const nameEl = document.createElement('span');
            nameEl.className = 'mcp-server-name';
            nameEl.textContent = server.name || server.id;

            const actions = document.createElement('div');
            actions.className = 'mcp-server-actions';

            const refreshBtn = document.createElement('button');
            refreshBtn.className = 'mcp-server-btn';
            refreshBtn.title = 'Refrescar';
            refreshBtn.textContent = '⟳';
            refreshBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._refreshMcpServer(server.id || server.name);
            });

            const toggle = document.createElement('label');
            toggle.className = 'mcp-server-toggle';
            const toggleInput = document.createElement('input');
            toggleInput.type = 'checkbox';
            toggleInput.checked = server.enabled !== false;
            toggleInput.addEventListener('change', (e) => {
                e.stopPropagation();
                this._toggleMcpServer(server.id || server.name, e.target.checked);
            });
            const toggleTrack = document.createElement('span');
            toggleTrack.className = 'toggle-track';
            toggle.appendChild(toggleInput);
            toggle.appendChild(toggleTrack);

            const statusDot = document.createElement('span');
            statusDot.className = `mcp-server-status ${statusClass}`;
            statusDot.title = statusDetail;

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'mcp-server-btn delete';
            deleteBtn.title = 'Eliminar';
            deleteBtn.innerHTML = SETTINGS_ICONS.close;
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._removeMcpServer(server.id || server.name);
            });

            actions.appendChild(refreshBtn);
            actions.appendChild(toggle);
            actions.appendChild(statusDot);
            actions.appendChild(deleteBtn);

            row.appendChild(expand);
            row.appendChild(nameEl);
            row.appendChild(actions);

            // ── Details ──
            const details = document.createElement('div');
            details.className = 'mcp-server-details';

            const transport = server.transport || 'stdio';
            const detailItems = [
                ['Transporte', transport],
                ['Estado', statusDetail],
            ];
            if (transport === 'stdio') {
                detailItems.push(['Comando', `${rt.resolved_command || server.command || '?'} ${(server.args || []).join(' ')}`]);
                if (server.cwd) detailItems.push(['CWD', server.cwd]);
            } else {
                detailItems.push(['URL', server.url || rt.url || '?']);
            }

            for (const [label, value] of detailItems) {
                const dRow = document.createElement('div');
                dRow.className = 'detail-row';
                const dLabel = document.createElement('span');
                dLabel.className = 'detail-label';
                dLabel.textContent = label;
                const dValue = document.createElement('span');
                dValue.className = 'detail-value';
                if (label === 'Estado' && !isReady && server.enabled) dValue.classList.add('error-text');
                dValue.textContent = value;
                dRow.appendChild(dLabel);
                dRow.appendChild(dValue);
                details.appendChild(dRow);
            }

            // Tools tags (from cache or runtime)
            const cachedTools = this._mcpToolsCache?.[server.id || server.name];
            if (cachedTools && cachedTools.length > 0) {
                const toolsContainer = document.createElement('div');
                toolsContainer.className = 'mcp-server-tools';
                for (const tool of cachedTools.slice(0, 12)) {
                    const tag = document.createElement('span');
                    tag.className = 'mcp-tool-tag';
                    tag.textContent = tool.name;
                    tag.title = tool.description || '';
                    toolsContainer.appendChild(tag);
                }
                if (cachedTools.length > 12) {
                    const more = document.createElement('span');
                    more.className = 'mcp-tool-tag';
                    more.textContent = `+${cachedTools.length - 12} mas`;
                    toolsContainer.appendChild(more);
                }
                details.appendChild(toolsContainer);
            }

            // Toggle expand
            row.addEventListener('click', () => {
                card.classList.toggle('expanded');
            });

            card.appendChild(row);
            card.appendChild(details);
            this.mcpServersList.appendChild(card);
        }

        // Update meta
        this._renderMcpMeta();
    }

    async _saveGatewayTelegramConfig() {
        const currentTelegram = this.currentGatewayChannels?.telegram || {};
        const nextChannels = {
            ...(this.currentGatewayChannels || {}),
            telegram: {
                ...currentTelegram,
                enabled: !!this.gatewayTelegramEnabledCheckbox?.checked,
                default_chat_id: String(this.gatewayTelegramDefaultChatInput?.value || '').trim(),
                allowed_chat_ids: this._parseMultilineList(this.gatewayTelegramAllowedChatsInput?.value || ''),
                bot_token_vault: currentTelegram.bot_token_vault || 'telegram_bot',
                poll_interval_seconds: Number(currentTelegram.poll_interval_seconds || 3.0),
                long_poll_timeout_seconds: Number(currentTelegram.long_poll_timeout_seconds || 20.0),
                base_url: currentTelegram.base_url || 'https://api.telegram.org',
            },
        };
        const ok = await this._saveConfigValue('gateway', 'channels', nextChannels);
        if (!ok) {
            this._renderGatewayTelegramMeta('No se pudo guardar la configuracion de Telegram.');
            return;
        }
        this.currentGatewayChannels = nextChannels;
        if (this.gatewayTelegramDefaultChatInput) {
            this.gatewayTelegramDefaultChatInput.value = nextChannels.telegram.default_chat_id || '';
        }
        if (this.gatewayTelegramAllowedChatsInput) {
            this.gatewayTelegramAllowedChatsInput.value = (nextChannels.telegram.allowed_chat_ids || []).join('\n');
        }
        try {
            const statusResp = await fetch(`${BACKEND_API}/gateway/status`);
            if (statusResp.ok) {
                this.gatewayRuntimeStatus = await statusResp.json();
            }
        } catch (err) {
            // Backend no listo
        }
        this._renderGatewayTelegramMeta('Configuracion de Telegram actualizada.');
    }

    async _saveGatewayTelegramCredential() {
        const token = String(this.gatewayTelegramTokenInput?.value || '').trim();
        if (!token) {
            this._renderGatewayTelegramMeta('Ingresa un bot token antes de guardarlo.');
            return;
        }
        try {
            const resp = await fetch(`${BACKEND_API}/gateway/credentials`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel: 'telegram', token }),
            });
            const data = await resp.json();
            if (!resp.ok || !data.configured) {
                throw new Error(data.detail || data.error || 'No se pudo guardar el token de Telegram');
            }
            this.currentGatewayTelegramCredential = data;
            if (this.gatewayTelegramTokenInput) {
                this.gatewayTelegramTokenInput.value = '';
                this.gatewayTelegramTokenInput.placeholder = 'Guardado en vault ✓';
            }
            try {
                const statusResp = await fetch(`${BACKEND_API}/gateway/status`);
                if (statusResp.ok) {
                    this.gatewayRuntimeStatus = await statusResp.json();
                }
            } catch (err) {
                // Backend no listo
            }
            this._renderGatewayTelegramMeta('Bot token guardado en vault.');
        } catch (err) {
            console.error('Error guardando token de Telegram:', err);
            this._renderGatewayTelegramMeta(`Error guardando token: ${err.message || err}`);
        }
    }

    async _saveGatewayWhatsAppConfig() {
        const currentWhatsApp = this.currentGatewayChannels?.whatsapp || {};
        const nextChannels = {
            ...(this.currentGatewayChannels || {}),
            whatsapp: {
                ...currentWhatsApp,
                enabled: !!this.gatewayWhatsAppEnabledCheckbox?.checked,
                default_chat_id: String(this.gatewayWhatsAppDefaultChatInput?.value || '').trim(),
                allowed_chat_ids: this._parseMultilineList(this.gatewayWhatsAppAllowedChatsInput?.value || ''),
                session_name: String(this.gatewayWhatsAppSessionNameInput?.value || '').trim() || 'default',
                node_executable: currentWhatsApp.node_executable || 'node',
                bridge_workdir: currentWhatsApp.bridge_workdir || 'assets/whatsapp-bridge',
                bridge_script_path: currentWhatsApp.bridge_script_path || 'assets/whatsapp-bridge/bridge.js',
                startup_timeout_seconds: Number(currentWhatsApp.startup_timeout_seconds || 45.0),
            },
        };
        const ok = await this._saveConfigValue('gateway', 'channels', nextChannels);
        if (!ok) {
            this._renderGatewayWhatsAppMeta('No se pudo guardar la configuracion de WhatsApp.');
            return;
        }
        this.currentGatewayChannels = nextChannels;
        if (this.gatewayWhatsAppDefaultChatInput) {
            this.gatewayWhatsAppDefaultChatInput.value = nextChannels.whatsapp.default_chat_id || '';
        }
        if (this.gatewayWhatsAppAllowedChatsInput) {
            this.gatewayWhatsAppAllowedChatsInput.value = (nextChannels.whatsapp.allowed_chat_ids || []).join('\n');
        }
        if (this.gatewayWhatsAppSessionNameInput) {
            this.gatewayWhatsAppSessionNameInput.value = nextChannels.whatsapp.session_name || 'default';
        }
        try {
            const [statusResp, runtimeResp] = await Promise.all([
                fetch(`${BACKEND_API}/gateway/status`),
                fetch(`${BACKEND_API}/gateway/runtime/whatsapp`),
            ]);
            if (statusResp.ok) {
                this.gatewayRuntimeStatus = await statusResp.json();
            }
            if (runtimeResp.ok) {
                const runtimeData = await runtimeResp.json();
                this.currentGatewayWhatsAppRuntime = runtimeData?.data && typeof runtimeData.data === 'object'
                    ? runtimeData.data
                    : {};
            }
        } catch (err) {
            // Backend no listo
        }
        this._renderGatewayWhatsAppMeta('Configuracion de WhatsApp actualizada.');
    }

    async _saveGatewayDiscordConfig() {
        const currentDiscord = this.currentGatewayChannels?.discord || {};
        const nextChannels = {
            ...(this.currentGatewayChannels || {}),
            discord: {
                ...currentDiscord,
                enabled: !!this.gatewayDiscordEnabledCheckbox?.checked,
                default_channel_id: String(this.gatewayDiscordDefaultChannelInput?.value || '').trim(),
                allowed_guild_ids: this._parseMultilineList(this.gatewayDiscordAllowedGuildsInput?.value || ''),
                allowed_channel_ids: this._parseMultilineList(this.gatewayDiscordAllowedChannelsInput?.value || ''),
                bot_token_vault: currentDiscord.bot_token_vault || 'discord_bot',
                group_activation_aliases: Array.isArray(currentDiscord.group_activation_aliases)
                    ? currentDiscord.group_activation_aliases
                    : ['g-mini', 'gmini', 'agente'],
                fake_mode: !!currentDiscord.fake_mode,
            },
        };
        const ok = await this._saveConfigValue('gateway', 'channels', nextChannels);
        if (!ok) {
            this._renderGatewayDiscordMeta('No se pudo guardar la configuracion de Discord.');
            return;
        }
        this.currentGatewayChannels = nextChannels;
        if (this.gatewayDiscordDefaultChannelInput) {
            this.gatewayDiscordDefaultChannelInput.value = nextChannels.discord.default_channel_id || '';
        }
        if (this.gatewayDiscordAllowedGuildsInput) {
            this.gatewayDiscordAllowedGuildsInput.value = (nextChannels.discord.allowed_guild_ids || []).join('\n');
        }
        if (this.gatewayDiscordAllowedChannelsInput) {
            this.gatewayDiscordAllowedChannelsInput.value = (nextChannels.discord.allowed_channel_ids || []).join('\n');
        }
        try {
            const [statusResp, runtimeResp] = await Promise.all([
                fetch(`${BACKEND_API}/gateway/status`),
                fetch(`${BACKEND_API}/gateway/runtime/discord`),
            ]);
            if (statusResp.ok) {
                this.gatewayRuntimeStatus = await statusResp.json();
            }
            if (runtimeResp.ok) {
                const runtimeData = await runtimeResp.json();
                this.currentGatewayDiscordRuntime = runtimeData?.data && typeof runtimeData.data === 'object'
                    ? runtimeData.data
                    : {};
            }
        } catch (err) {
            // Backend no listo
        }
        this._renderGatewayDiscordMeta('Configuracion de Discord actualizada.');
    }

    async _saveGatewayDiscordCredential() {
        const token = String(this.gatewayDiscordTokenInput?.value || '').trim();
        if (!token) {
            this._renderGatewayDiscordMeta('Ingresa un bot token antes de guardarlo.');
            return;
        }
        try {
            const resp = await fetch(`${BACKEND_API}/gateway/credentials`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel: 'discord', token }),
            });
            const data = await resp.json();
            if (!resp.ok || !data.configured) {
                throw new Error(data.detail || data.error || 'No se pudo guardar el token de Discord');
            }
            this.currentGatewayDiscordCredential = data;
            if (this.gatewayDiscordTokenInput) {
                this.gatewayDiscordTokenInput.value = '';
                this.gatewayDiscordTokenInput.placeholder = 'Guardado en vault ✓';
            }
            try {
                const [statusResp, runtimeResp] = await Promise.all([
                    fetch(`${BACKEND_API}/gateway/status`),
                    fetch(`${BACKEND_API}/gateway/runtime/discord`),
                ]);
                if (statusResp.ok) {
                    this.gatewayRuntimeStatus = await statusResp.json();
                }
                if (runtimeResp.ok) {
                    const runtimeData = await runtimeResp.json();
                    this.currentGatewayDiscordRuntime = runtimeData?.data && typeof runtimeData.data === 'object'
                        ? runtimeData.data
                        : {};
                }
            } catch (err) {
                // Backend no listo
            }
            this._renderGatewayDiscordMeta('Bot token guardado en vault.');
        } catch (err) {
            console.error('Error guardando token de Discord:', err);
            this._renderGatewayDiscordMeta(`Error guardando token: ${err.message || err}`);
        }
    }

    async _saveSkillsConfig() {
        const preferred = this._parseMultilineList(this.skillsPreferredInput?.value || '');
        const customPaths = this._parseMultilineList(this.skillsPathsInput?.value || '');
        await this._saveConfigValue('skills', 'enabled', !!this.skillsEnabledCheckbox?.checked);
        await this._saveConfigValue('skills', 'preferred', preferred);
        await this._saveConfigValue('skills', 'custom_paths', customPaths);
        this.currentSkillsPreferred = preferred;
        this.currentSkillsPaths = customPaths;
        if (this.skillsPreferredInput) {
            this.skillsPreferredInput.value = preferred.join('\n');
        }
        if (this.skillsPathsInput) {
            this.skillsPathsInput.value = customPaths.join('\n');
        }
        await this._syncSkillsCatalog();
        this._renderSkillsMeta();
    }

    async _syncAppRuntimeSettings() {
        if (!window.gmini?.getAppRuntimeSettings) return null;
        try {
            this.appRuntimeState = await window.gmini.getAppRuntimeSettings();
        } catch (err) {
            console.error('Error sincronizando runtime de la app:', err);
            this.appRuntimeState = null;
        }
        return this.appRuntimeState;
    }

    async _saveAppBehaviorSettings() {
        const startWithWindows = !!this.appStartWithWindowsCheckbox?.checked;
        const minimizeToTray = !!this.appMinimizeToTrayCheckbox?.checked;
        const closeToTray = !!this.appCloseToTrayCheckbox?.checked;
        const startHiddenToTray = !!this.appStartHiddenToTrayCheckbox?.checked;

        const operations = [
            ['start_with_windows', startWithWindows],
            ['minimize_to_tray', minimizeToTray],
            ['close_to_tray', closeToTray],
            ['start_hidden_to_tray', startHiddenToTray],
        ];

        let allSaved = true;
        for (const [key, value] of operations) {
            const ok = await this._saveConfigValue('app', key, value);
            allSaved = allSaved && ok;
        }

        if (!allSaved) {
            this._renderAppBehaviorMeta('No se pudo guardar toda la configuracion del modo 24/7.');
            return;
        }

        this.currentStartWithWindows = startWithWindows;
        this.currentMinimizeToTray = minimizeToTray;
        this.currentCloseToTray = closeToTray;
        this.currentStartHiddenToTray = startHiddenToTray;

        if (window.gmini?.reloadAppRuntimeSettings) {
            try {
                this.appRuntimeState = await window.gmini.reloadAppRuntimeSettings();
            } catch (err) {
                console.error('Error recargando runtime de la app:', err);
            }
        } else {
            await this._syncAppRuntimeSettings();
        }

        this._renderAppBehaviorMeta('Modo 24/7 actualizado.');
    }

    async _populateAvatarSkinOptions(selectedSkin, charType) {
        const select = document.getElementById('select-avatar-skin');
        if (!select) return;
        const group = charType === '2d' ? '2d' : (charType === 'none' ? 'none' : '3d');
        let skins = [{ id: 'energy-ball', name: 'Bola de energia', group: '3d' }];
        try {
            if (window.gmini && typeof window.gmini.skinList === 'function') {
                const list = await window.gmini.skinList();
                if (Array.isArray(list) && list.length) skins = list;
            }
        } catch (err) {
            // Si IPC no esta disponible, mantenemos la opcion por defecto
        }
        const filtered = group === 'none' ? [] : skins.filter((s) => s.group === group);
        select.innerHTML = '';
        for (const skin of filtered) {
            const option = document.createElement('option');
            option.value = skin.id;
            option.textContent = skin.name || skin.id;
            select.appendChild(option);
        }
        if (selectedSkin && filtered.some((s) => s.id === selectedSkin)) {
            select.value = selectedSkin;
        }
        select.disabled = group === 'none';
        select.closest('.setting-group')?.classList.toggle('disabled-group', group === 'none');
    }

    _setupCharacterCreator() {
        const btnToggle = document.getElementById('btn-toggle-new-character');
        const form = document.getElementById('new-character-form');
        const typeSelect = document.getElementById('select-new-character-type');
        const pane3d = document.getElementById('new-character-3d-pane');
        const pane2d = document.getElementById('new-character-2d-pane');
        const btnToggleEmotions = document.getElementById('btn-toggle-character-emotions');
        const emotionsPane = document.getElementById('new-character-emotions-pane');
        const nameInput = document.getElementById('input-new-character-name');
        const btnCreate = document.getElementById('btn-create-character');
        const btnCancel = document.getElementById('btn-cancel-new-character');
        const statusEl = document.getElementById('new-character-status');
        const btnPickModel = document.getElementById('btn-pick-character-model');
        const modelInput = document.getElementById('input-character-model');
        if (!btnToggle || !form) return;

        const basename = (p) => String(p || '').split(/[\\/]/).pop();

        const resetCreatorForm = () => {
            this.newCharacterDraft = { model: '', sprites: {} };
            if (nameInput) nameInput.value = '';
            if (typeSelect) typeSelect.value = '3d';
            if (modelInput) modelInput.value = '';
            form.querySelectorAll('#new-character-2d-pane input.setting-input').forEach((inp) => { inp.value = ''; });
            pane3d?.classList.remove('hidden');
            pane2d?.classList.add('hidden');
            emotionsPane?.classList.add('hidden');
            if (statusEl) statusEl.textContent = '';
        };
        resetCreatorForm();

        btnToggle.addEventListener('click', () => {
            const willShow = form.classList.contains('hidden');
            if (willShow) resetCreatorForm();
            form.classList.toggle('hidden', !willShow);
        });

        btnCancel?.addEventListener('click', () => {
            form.classList.add('hidden');
            resetCreatorForm();
        });

        typeSelect?.addEventListener('change', (e) => {
            const is2d = e.target.value === '2d';
            pane3d?.classList.toggle('hidden', is2d);
            pane2d?.classList.toggle('hidden', !is2d);
        });

        btnToggleEmotions?.addEventListener('click', () => {
            emotionsPane?.classList.toggle('hidden');
        });

        btnPickModel?.addEventListener('click', async () => {
            if (!window.gmini?.skinPickFile) return;
            const filePath = await window.gmini.skinPickFile('model');
            if (!filePath) return;
            this.newCharacterDraft.model = filePath;
            if (modelInput) modelInput.value = basename(filePath);
        });

        form.querySelectorAll('.btn-pick-sprite').forEach((btn) => {
            btn.addEventListener('click', async () => {
                if (!window.gmini?.skinPickFile) return;
                const key = btn.dataset.sprite;
                const filePath = await window.gmini.skinPickFile('sprite');
                if (!filePath) return;
                this.newCharacterDraft.sprites[key] = filePath;
                const input = document.getElementById(`input-sprite-${key}`);
                if (input) input.value = basename(filePath);
            });
        });

        btnCreate?.addEventListener('click', async () => {
            const name = nameInput?.value.trim() || '';
            const type = typeSelect?.value === '2d' ? '2d' : '3d';
            if (statusEl) statusEl.textContent = '';

            if (!name) {
                if (statusEl) statusEl.textContent = 'Ingresa un nombre para el personaje.';
                return;
            }

            const payload = { name, group: type };
            if (type === '3d') {
                if (!this.newCharacterDraft.model) {
                    if (statusEl) statusEl.textContent = 'Selecciona un archivo de modelo (.vrm o .glb).';
                    return;
                }
                payload.model = this.newCharacterDraft.model;
            } else {
                const required = ['idle', 'talk', 'blink', 'blink_talk'];
                const missing = required.filter((k) => !this.newCharacterDraft.sprites[k]);
                if (missing.length) {
                    if (statusEl) statusEl.textContent = `Faltan sprites obligatorios: ${missing.join(', ')}.`;
                    return;
                }
                const sprites = {};
                for (const k of required) sprites[k] = this.newCharacterDraft.sprites[k];
                const emotions = {};
                for (const k of ['happy', 'sad', 'angry', 'surprised', 'relaxed']) {
                    if (this.newCharacterDraft.sprites[k]) emotions[k] = this.newCharacterDraft.sprites[k];
                }
                if (Object.keys(emotions).length) sprites.emotions = emotions;
                payload.sprites = sprites;
            }

            btnCreate.disabled = true;
            if (statusEl) statusEl.textContent = 'Creando personaje...';
            try {
                const result = await window.gmini.skinCreate(payload);
                if (!result?.ok) {
                    const messages = {
                        'invalid-group': 'Tipo invalido.',
                        'invalid-name': 'Nombre invalido.',
                        'duplicate': 'Ya existe un personaje con ese nombre.',
                        'missing-model': 'Falta el archivo de modelo.',
                        'missing-sprites': 'Faltan sprites obligatorios.',
                        'copy-failed': 'Error al copiar los archivos.',
                    };
                    if (statusEl) statusEl.textContent = messages[result?.error] || 'No se pudo crear el personaje.';
                    return;
                }

                const selCharType = document.getElementById('select-character-type');
                if (selCharType) selCharType.value = type;
                await this._populateAvatarSkinOptions(result.skin?.id, type);
                await this._saveConfigValue('character', 'type', type);
                if (result.skin?.id) {
                    await this._saveConfigValue('character', 'skin', result.skin.id);
                }

                form.classList.add('hidden');
                resetCreatorForm();
            } catch (err) {
                if (statusEl) statusEl.textContent = 'No se pudo crear el personaje.';
            } finally {
                btnCreate.disabled = false;
            }
        });
    }

    async _saveVoiceCharacterSettings() {
        const voiceDraft = this._readVoiceDraftFromControls();
        this.voiceDraft = voiceDraft;
        this.voiceDraftDirty = this._hasVoicePendingChanges();
        const ttsEngine = voiceDraft.tts_primary || 'melotts';
        const elevenVoiceId = voiceDraft.elevenlabs_voice_id || '';
        const googleVoice = voiceDraft.google_voice || 'Kore';
        const ttsSpeed = voiceDraft.tts_speed;
        const charType = document.getElementById('select-character-type')?.value || '3d';
        const charSkin = document.getElementById('select-avatar-skin')?.value || 'energy-ball';
        const autoTts = !!voiceDraft.auto_tts;
        const voiceEnabled = !!voiceDraft.enabled;
        const debugPayload = {
            tts_primary: ttsEngine,
            elevenlabs_voice_id: elevenVoiceId,
            google_voice: googleVoice,
            tts_speed: ttsSpeed,
            auto_tts: autoTts,
            enabled: voiceEnabled,
            character_type: charType,
            character_skin: charSkin,
        };
        this._voiceDebug('voice-settings:save:start', {
            payload: debugPayload,
            draftBeforeSave: this._cloneVoiceDebug(voiceDraft),
            persistedBeforeSave: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
            runtimeBeforeSave: this._cloneVoiceDebug(this.voiceMetadata?.runtime || null),
        });

        const ops = [
            ['voice', 'tts_primary', ttsEngine],
            ['voice', 'elevenlabs_voice_id', elevenVoiceId],
            ['voice', 'google_voice', googleVoice],
            ['voice', 'tts_speed', ttsSpeed],
            ['voice', 'auto_tts', autoTts],
            ['voice', 'enabled', voiceEnabled],
            ['character', 'type', charType],
            ['character', 'skin', charSkin],
        ];

        let allOk = true;
        for (const [section, key, value] of ops) {
            this._voiceDebug('voice-settings:save:operation', { section, key, value });
            const ok = await this._saveConfigValue(section, key, value);
            allOk = allOk && ok;
        }

        await this._checkApiKeyStatus();
        await this._syncVoiceMetadata({ preserveDraft: false, reason: 'voice-settings:save:commit' });
        this._voiceDebug('voice-settings:save:done', {
            allOk,
            payload: debugPayload,
            persistedAfterSave: this._cloneVoiceDebug(this.voiceMetadata?.settings || null),
            runtimeAfterSave: this._cloneVoiceDebug(this.voiceMetadata?.runtime || null),
            draftAfterSave: this._cloneVoiceDebug(this.voiceDraft),
        });
        const metaEl = this.voiceMeta;
        if (metaEl) {
            metaEl.textContent = allOk
                ? `Configuracion de voz y personaje guardada. ${this._buildVoiceRuntimeText()}`
                : 'Error al guardar algunas opciones de voz.';
        }
    }

    async _saveSchedulerSettings() {
        const pollInterval = Number(this.schedulerPollIntervalInput?.value || 2.0);
        const normalizedPoll = Number.isFinite(pollInterval) && pollInterval >= 0.5 ? pollInterval : 2.0;
        await this._saveConfigValue('scheduler', 'enabled', !!this.schedulerEnabledCheckbox?.checked);
        await this._saveConfigValue('scheduler', 'poll_interval_seconds', normalizedPoll);
        this.currentSchedulerEnabled = !!this.schedulerEnabledCheckbox?.checked;
        this.currentSchedulerPollInterval = normalizedPoll;
        if (this.schedulerPollIntervalInput) {
            this.schedulerPollIntervalInput.value = String(normalizedPoll);
        }
        this._renderSchedulerSettingsMeta();
    }

    async _saveBudgetSettings() {
        const dailyLimit = Number(this.budgetDailyLimitInput?.value || 0);
        const monthlyLimit = Number(this.budgetMonthlyLimitInput?.value || 0);
        const warningThreshold = Number(this.budgetWarningThresholdInput?.value || 80);
        const taskLimit = Number(this.budgetTaskLimitInput?.value || 0);
        const subagentLimit = Number(this.budgetSubagentLimitInput?.value || 0);
        const subagentShare = Number(this.budgetSubagentShareInput?.value || 0);

        const normalizedDaily = Number.isFinite(dailyLimit) && dailyLimit >= 0 ? dailyLimit : 0;
        const normalizedMonthly = Number.isFinite(monthlyLimit) && monthlyLimit >= 0 ? monthlyLimit : 0;
        const normalizedWarning = Number.isFinite(warningThreshold) && warningThreshold >= 1 && warningThreshold <= 100
            ? Math.round(warningThreshold)
            : 80;
        const normalizedTask = Number.isFinite(taskLimit) && taskLimit >= 0 ? taskLimit : 0;
        const normalizedSubagentLimit = Number.isFinite(subagentLimit) && subagentLimit >= 0 ? subagentLimit : 0;
        const normalizedSubagentShare = Number.isFinite(subagentShare) && subagentShare >= 0 && subagentShare <= 1
            ? Number(subagentShare.toFixed(2))
            : 0;
        let modeLimits = {};
        try {
            modeLimits = this._parseJsonConfig(this.budgetModeLimitsInput?.value || '{}', {});
            if (!modeLimits || typeof modeLimits !== 'object' || Array.isArray(modeLimits)) {
                throw new Error('Los límites por modo deben ser un objeto JSON.');
            }
        } catch (err) {
            this._renderBudgetMeta(`JSON inválido en límites por modo: ${err.message || err}`);
            return;
        }

        await this._saveConfigValue('budget', 'enabled', !!this.budgetEnabledCheckbox?.checked);
        await this._saveConfigValue('budget', 'daily_limit_usd', normalizedDaily);
        await this._saveConfigValue('budget', 'monthly_limit_usd', normalizedMonthly);
        await this._saveConfigValue('budget', 'warning_threshold_percent', normalizedWarning);
        await this._saveConfigValue('budget', 'subagent_default_task_limit_usd', normalizedSubagentLimit);
        await this._saveConfigValue('budget', 'subagent_parent_budget_share', normalizedSubagentShare);
        await this._saveConfigValue('budget', 'mode_task_limits_usd', modeLimits);
        const nextHardLimits = {
            ...(this.currentModelRouterHardLimits || {}),
            max_cost_per_task_usd: normalizedTask,
        };
        await this._saveConfigValue('model_router', 'hard_limits', nextHardLimits);

        this.currentBudgetEnabled = !!this.budgetEnabledCheckbox?.checked;
        this.currentBudgetDailyLimit = normalizedDaily;
        this.currentBudgetMonthlyLimit = normalizedMonthly;
        this.currentBudgetWarningThreshold = normalizedWarning;
        this.currentTaskBudgetLimit = normalizedTask;
        this.currentBudgetSubagentLimit = normalizedSubagentLimit;
        this.currentBudgetSubagentShare = normalizedSubagentShare;
        this.currentBudgetModeLimits = modeLimits;
        this.currentModelRouterHardLimits = nextHardLimits;

        if (this.budgetDailyLimitInput) this.budgetDailyLimitInput.value = String(normalizedDaily);
        if (this.budgetMonthlyLimitInput) this.budgetMonthlyLimitInput.value = String(normalizedMonthly);
        if (this.budgetWarningThresholdInput) this.budgetWarningThresholdInput.value = String(normalizedWarning);
        if (this.budgetTaskLimitInput) this.budgetTaskLimitInput.value = String(normalizedTask);
        if (this.budgetSubagentLimitInput) this.budgetSubagentLimitInput.value = String(normalizedSubagentLimit);
        if (this.budgetSubagentShareInput) this.budgetSubagentShareInput.value = String(normalizedSubagentShare);
        if (this.budgetModeLimitsInput) this.budgetModeLimitsInput.value = JSON.stringify(modeLimits, null, 2);

        this._renderBudgetMeta();
    }

    async _savePaymentsSettings() {
        const enabled = !!this.paymentsEnabledCheckbox?.checked;
        const mode = String(this.spendPermissionsModeSelect?.value || 'ask_always').trim();
        const allowedModes = new Set(['deny_all', 'ask_always', 'ask_above_x', 'auto_approve_under_x']);
        const normalizedMode = allowedModes.has(mode) ? mode : 'ask_always';
        const askAbove = Number(this.spendAskAboveInput?.value || 0);
        const autoApproveUnder = Number(this.spendAutoApproveUnderInput?.value || 0);
        const normalizedAskAbove = Number.isFinite(askAbove) && askAbove >= 0 ? askAbove : 0;
        const normalizedAutoApproveUnder = Number.isFinite(autoApproveUnder) && autoApproveUnder >= 0 ? autoApproveUnder : 0;

        await this._saveConfigValue('payments', 'enabled', enabled);
        await this._saveConfigValue('payments', 'spend_permissions_mode', normalizedMode);
        await this._saveConfigValue('payments', 'ask_above_usd', normalizedAskAbove);
        await this._saveConfigValue('payments', 'auto_approve_under_usd', normalizedAutoApproveUnder);

        this.currentPaymentsEnabled = enabled;
        this.currentSpendPermissionsMode = normalizedMode;
        this.currentSpendAskAboveUsd = normalizedAskAbove;
        this.currentSpendAutoApproveUnderUsd = normalizedAutoApproveUnder;

        if (this.spendPermissionsModeSelect) this.spendPermissionsModeSelect.value = normalizedMode;
        if (this.spendAskAboveInput) this.spendAskAboveInput.value = String(normalizedAskAbove);
        if (this.spendAutoApproveUnderInput) this.spendAutoApproveUnderInput.value = String(normalizedAutoApproveUnder);

        this._renderPaymentsMeta();
    }

    async _savePaymentAccountsSettings() {
        const defaultAccountId = String(this.paymentsDefaultAccountInput?.value || '').trim();
        let accounts = [];
        try {
            accounts = this._parseJsonConfig(this.paymentsAccountsInput?.value || '[]', []);
            if (!Array.isArray(accounts)) {
                throw new Error('Las cuentas de pago deben ser una lista JSON.');
            }
        } catch (err) {
            this._renderPaymentAccountsMeta(`JSON inválido en cuentas de pago: ${err.message || err}`);
            return;
        }

        await this._saveConfigValue('payments', 'default_account_id', defaultAccountId);
        await this._saveConfigValue('payments', 'accounts', accounts);

        this.currentDefaultPaymentAccountId = defaultAccountId;
        this.currentPaymentAccounts = accounts;

        if (this.paymentsDefaultAccountInput) this.paymentsDefaultAccountInput.value = defaultAccountId;
        if (this.paymentsAccountsInput) this.paymentsAccountsInput.value = JSON.stringify(accounts, null, 2);

        this._renderPaymentAccountsMeta();
    }

    async _saveConfigValue(section, key, value) {
        try {
            const resp = await fetch(`${BACKEND_API}/config`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ section, key, value }),
            });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || data.detail || 'No se pudo guardar la configuracion');
            }
            return true;
        } catch (err) {
            console.error(`Error guardando config ${section}.${key}:`, err);
            return false;
        }
    }

    _renderAppBehaviorMeta(statusMessage = '') {
        if (!this.appBehaviorMeta) return;
        const startWithWindows = !!this.appStartWithWindowsCheckbox?.checked;
        const minimizeToTray = !!this.appMinimizeToTrayCheckbox?.checked;
        const closeToTray = !!this.appCloseToTrayCheckbox?.checked;
        const startHiddenToTray = !!this.appStartHiddenToTrayCheckbox?.checked;
        const runtime = this.appRuntimeState || {};

        const parts = [
            startWithWindows ? 'Inicio con Windows solicitado.' : 'Inicio con Windows desactivado.',
            minimizeToTray ? 'Minimizar enviara la app a la bandeja.' : 'Minimizar usara el comportamiento normal de ventana.',
            closeToTray ? 'Cerrar dejara G-Mini corriendo en segundo plano.' : 'Cerrar saldra de la app.',
            startHiddenToTray ? 'El inicio oculto aplica en el proximo arranque.' : 'La app se mostrara al iniciar.',
        ];

        if (startWithWindows) {
            if (runtime.canApplyStartWithWindows) {
                parts.push(runtime.startWithWindowsApplied
                    ? 'El registro de inicio automatico esta activo.'
                    : 'El registro de inicio automatico se aplicara tras guardar.');
            } else if (runtime.platform === 'win32') {
                parts.push('Esta build no empaquetada no puede registrar inicio automatico real todavia.');
            } else {
                parts.push('Inicio automatico solo se aplica en Windows.');
            }
        }

        this.appBehaviorMeta.textContent = `${statusMessage ? `${statusMessage} ` : ''}${parts.join(' ')}`.trim();
    }

    _renderAutonomyScopeMeta() {
        if (!this.autonomyScopeMeta) return;
        const descriptions = {
            baja: 'Reactivo: ejecuta solo la tarea exacta que pides, sin pasos extra ni iniciativa.',
            media: 'Equilibrado: completa la tarea solicitada incluyendo los sub-pasos necesarios.',
            alta: 'Proactivo: encadena planes de varios pasos y propone acciones de seguimiento.',
        };
        this.autonomyScopeMeta.textContent = descriptions[this.currentAutonomy] || descriptions.media;
    }

    _renderAutonomyMeta() {
        if (!this.autonomyMeta) return;
        const descriptions = {
            libre: 'Ejecuta sin pedir aprobación manual, salvo bloqueos directos de política.',
            supervisado: 'Solo pide aprobación cuando la acción es sensible o la confianza es baja.',
            asistido: 'Todo plan de acción queda pendiente hasta que tú lo apruebes.',
        };
        this.autonomyMeta.textContent = descriptions[this.currentAutonomyLevel] || descriptions.supervisado;
    }

    _renderBlockedSitesMeta() {
        if (!this.blockedSitesMeta) return;
        const enabled = !!this.blockedSitesEnabledCheckbox?.checked;
        const count = this._parseMultilineList(this.blockedSitesInput?.value || '').length;
        this.blockedSitesMeta.textContent = enabled
            ? `Bloqueo activo para ${count} dominio(s).`
            : 'Bloqueo desactivado. La lista se conserva pero no se aplica.';
    }

    _renderExecApprovalsMeta() {
        if (!this.execApprovalsMeta) return;
        const enabled = !!this.execApprovalsEnabledCheckbox?.checked;
        const hostKey = this._normalizeHostKey(
            this.execApprovalsHostInput?.value || this.currentExecApprovalHostKey || 'default-host'
        );
        const allowedCommands = this._parseMultilineList(this.execAllowedCommandsInput?.value || '').length;
        const allowedPatterns = this._parseMultilineList(this.execAllowedPatternsInput?.value || '').length;
        const deniedPatterns = this._parseMultilineList(this.execDeniedPatternsInput?.value || '').length;
        this.execApprovalsMeta.textContent = enabled
            ? `Whitelisting activo en ${hostKey}: ${allowedCommands} comando(s), ${allowedPatterns} patron(es) permitidos y ${deniedPatterns} patron(es) denegados.`
            : `Whitelisting desactivado en ${hostKey}. La configuracion se conserva pero no se aplica.`;
    }

    _renderMcpMeta() {
        if (!this.mcpMeta) return;
        const enabled = !!this.mcpEnabledCheckbox?.checked;
        const count = this.currentMcpServers.length;
        const ready = this.mcpRuntimeServers.filter((s) => s.ready).length;
        const issues = this.mcpRuntimeServers.filter((s) => !s.ready && s.status !== 'disabled' && s.status !== 'globally_disabled').length;
        if (!enabled) {
            this.mcpMeta.textContent = `MCP desactivado. ${count} servidor(es) configurado(s).`;
        } else if (count === 0) {
            this.mcpMeta.textContent = 'MCP activo. Agrega un servidor para comenzar.';
        } else {
            this.mcpMeta.textContent = `${ready} de ${count} servidor(es) listo(s)${issues ? `, ${issues} con errores` : ''}.`;
        }
    }

    _renderGatewayTelegramMeta(statusMessage = '') {
        if (!this.gatewayTelegramMeta) return;
        const enabled = !!this.gatewayTelegramEnabledCheckbox?.checked;
        const allowedCount = this._parseMultilineList(this.gatewayTelegramAllowedChatsInput?.value || '').length;
        const defaultChat = String(this.gatewayTelegramDefaultChatInput?.value || '').trim() || 'sin default_chat_id';
        const credential = this.currentGatewayTelegramCredential || { configured: false, masked: null };
        const channels = Array.isArray(this.gatewayRuntimeStatus?.channels) ? this.gatewayRuntimeStatus.channels : [];
        const telegramState = channels.find((item) => item.channel === 'telegram') || {};
        const runtimeText = telegramState.detail ? ` Runtime: ${telegramState.detail}` : '';
        const tokenText = credential.configured
            ? `Token configurado ${credential.masked ? `(${credential.masked})` : 'en vault'}.`
            : 'Sin token en vault.';
        const baseText = enabled
            ? `Telegram activo para gateway. Default chat: ${defaultChat}. Chat IDs autorizados: ${allowedCount}. ${tokenText}${runtimeText}`
            : `Telegram desactivado. Se conserva la configuracion de chat y credenciales. ${tokenText}${runtimeText}`;
        this.gatewayTelegramMeta.textContent = `${statusMessage ? `${statusMessage} ` : ''}${baseText}`.trim();
    }

    _renderGatewayWhatsAppMeta(statusMessage = '') {
        if (!this.gatewayWhatsAppMeta) return;
        const enabled = !!this.gatewayWhatsAppEnabledCheckbox?.checked;
        const allowedCount = this._parseMultilineList(this.gatewayWhatsAppAllowedChatsInput?.value || '').length;
        const defaultChat = String(this.gatewayWhatsAppDefaultChatInput?.value || '').trim() || 'sin default_chat_id';
        const sessionName = String(this.gatewayWhatsAppSessionNameInput?.value || '').trim() || 'default';
        const channels = Array.isArray(this.gatewayRuntimeStatus?.channels) ? this.gatewayRuntimeStatus.channels : [];
        const whatsappState = channels.find((item) => item.channel === 'whatsapp') || {};
        const runtime = this.currentGatewayWhatsAppRuntime && typeof this.currentGatewayWhatsAppRuntime === 'object'
            ? this.currentGatewayWhatsAppRuntime
            : {};
        const runtimeStatus = String(runtime.status || whatsappState.status || '').trim() || 'desconocido';
        const runtimeDetail = String(whatsappState.detail || runtime.error || '').trim();
        const qrDataUrl = String(runtime.qr_data_url || '').trim();

        if (this.gatewayWhatsAppQrImage) {
            if (qrDataUrl) {
                this.gatewayWhatsAppQrImage.src = qrDataUrl;
                this.gatewayWhatsAppQrImage.style.display = 'block';
            } else {
                this.gatewayWhatsAppQrImage.removeAttribute('src');
                this.gatewayWhatsAppQrImage.style.display = 'none';
            }
        }

        const baseText = enabled
            ? `WhatsApp activo para gateway. Sesion: ${sessionName}. Default chat: ${defaultChat}. Chat IDs autorizados: ${allowedCount}. Runtime: ${runtimeStatus}.${runtimeDetail ? ` ${runtimeDetail}` : ''}${qrDataUrl ? ' QR disponible para escanear.' : ''}`
            : `WhatsApp desactivado. Se conserva la configuracion de chat y sesion (${sessionName}). Runtime: ${runtimeStatus}.${runtimeDetail ? ` ${runtimeDetail}` : ''}`;
        this.gatewayWhatsAppMeta.textContent = `${statusMessage ? `${statusMessage} ` : ''}${baseText}`.trim();
    }

    _renderGatewayDiscordMeta(statusMessage = '') {
        if (!this.gatewayDiscordMeta) return;
        const enabled = !!this.gatewayDiscordEnabledCheckbox?.checked;
        const allowedGuilds = this._parseMultilineList(this.gatewayDiscordAllowedGuildsInput?.value || '').length;
        const allowedChannels = this._parseMultilineList(this.gatewayDiscordAllowedChannelsInput?.value || '').length;
        const defaultChannel = String(this.gatewayDiscordDefaultChannelInput?.value || '').trim() || 'sin default_channel_id';
        const credential = this.currentGatewayDiscordCredential || { configured: false, masked: null };
        const channels = Array.isArray(this.gatewayRuntimeStatus?.channels) ? this.gatewayRuntimeStatus.channels : [];
        const discordState = channels.find((item) => item.channel === 'discord') || {};
        const runtime = this.currentGatewayDiscordRuntime && typeof this.currentGatewayDiscordRuntime === 'object'
            ? this.currentGatewayDiscordRuntime
            : {};
        const runtimeStatus = String(runtime.status || discordState.status || '').trim() || 'desconocido';
        const runtimeDetail = String(discordState.detail || runtime.error || '').trim();
        const tokenText = credential.configured
            ? `Token configurado ${credential.masked ? `(${credential.masked})` : 'en vault'}.`
            : 'Sin token en vault.';
        const fakeText = runtime.fake_mode ? ' Runtime fake activo.' : '';
        const identityText = runtime.bot_user ? ` Bot: ${runtime.bot_user}.` : '';
        const baseText = enabled
            ? `Discord activo para gateway. Default channel: ${defaultChannel}. Guild IDs autorizados: ${allowedGuilds}. Channel IDs autorizados: ${allowedChannels}. ${tokenText} Runtime: ${runtimeStatus}.${runtimeDetail ? ` ${runtimeDetail}` : ''}${identityText}${fakeText}`
            : `Discord desactivado. Se conserva la configuracion de canales y credenciales. ${tokenText} Runtime: ${runtimeStatus}.${runtimeDetail ? ` ${runtimeDetail}` : ''}${identityText}${fakeText}`;
        this.gatewayDiscordMeta.textContent = `${statusMessage ? `${statusMessage} ` : ''}${baseText}`.trim();
    }

    _renderSkillsMeta() {
        if (!this.skillsMeta) return;
        const enabled = !!this.skillsEnabledCheckbox?.checked;
        const preferred = this._parseMultilineList(this.skillsPreferredInput?.value || '').length;
        const customPaths = this._parseMultilineList(this.skillsPathsInput?.value || '').length;
        const discovered = Array.isArray(this.discoveredSkills) ? this.discoveredSkills.length : 0;
        const existingRoots = this.skillRoots.filter((item) => item.exists).length;
        const preview = this.discoveredSkills
            .slice(0, 3)
            .map((item) => item.id || item.name)
            .join(', ');
        this.skillsMeta.textContent = enabled
            ? `Skills activas: ${discovered} descubierta(s) en ${existingRoots}/${this.skillRoots.length} roots, ${preferred} preferida(s) y ${customPaths} ruta(s) personalizadas.${preview ? ` Ejemplos: ${preview}` : ''}`
            : `Skills configurables desactivados. Se conservan ${customPaths} ruta(s) y se detectaron ${discovered} skill(s).${preview ? ` Ejemplos: ${preview}` : ''}`;
    }

    _renderSchedulerSettingsMeta() {
        if (!this.schedulerSettingsMeta) return;
        const enabled = !!this.schedulerEnabledCheckbox?.checked;
        const poll = Number(this.schedulerPollIntervalInput?.value || this.currentSchedulerPollInterval || 2.0);
        this.schedulerSettingsMeta.textContent = enabled
            ? `Scheduler activo. Polling aproximado cada ${poll}s para detectar jobs vencidos.`
            : `Scheduler desactivado. Los jobs se conservan, pero el loop de ejecucion no corre.`;
    }

    _renderBudgetMeta(statusMessage = '') {
        if (!this.budgetMeta) return;
        const enabled = !!this.budgetEnabledCheckbox?.checked;
        const daily = Number(this.budgetDailyLimitInput?.value || this.currentBudgetDailyLimit || 0);
        const monthly = Number(this.budgetMonthlyLimitInput?.value || this.currentBudgetMonthlyLimit || 0);
        const warning = Number(this.budgetWarningThresholdInput?.value || this.currentBudgetWarningThreshold || 80);
        const task = Number(this.budgetTaskLimitInput?.value || this.currentTaskBudgetLimit || 0);
        const subagent = Number(this.budgetSubagentLimitInput?.value || this.currentBudgetSubagentLimit || 0);
        const share = Number(this.budgetSubagentShareInput?.value || this.currentBudgetSubagentShare || 0);
        let modeSummary = '';
        try {
            const parsed = this._parseJsonConfig(this.budgetModeLimitsInput?.value || '{}', {});
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                const entries = Object.entries(parsed);
                if (entries.length > 0) {
                    modeSummary = entries.map(([k, v]) => `${k}: $${v}`).join(', ');
                } else {
                    modeSummary = 'ninguno';
                }
            }
        } catch (err) {
            this.budgetMeta.textContent = `JSON inválido en límites por modo: ${err.message || err}`;
            return;
        }
        const baseText = enabled
            ? `Monitor activo. Alertará desde ${warning}% y podrá frenar tareas al superar: tarea $${task || 0}, día $${daily || 0}, mes $${monthly || 0}, subagente $${subagent || 0} o share ${share || 0} del presupuesto padre. Modos: ${modeSummary}.`
            : `Monitor de costos desactivado. Límites conservados: tarea $${task || 0}, día $${daily || 0}, mes $${monthly || 0}. Modos: ${modeSummary}.`;
        this.budgetMeta.textContent = `${statusMessage ? `${statusMessage} ` : ''}${baseText}`.trim();
    }

    _renderPaymentsMeta(statusMessage = '') {
        if (!this.paymentsMeta) return;
        const enabled = !!this.paymentsEnabledCheckbox?.checked;
        const mode = String(this.spendPermissionsModeSelect?.value || this.currentSpendPermissionsMode || 'ask_always');
        const askAbove = Number(this.spendAskAboveInput?.value || this.currentSpendAskAboveUsd || 0);
        const autoApproveUnder = Number(this.spendAutoApproveUnderInput?.value || this.currentSpendAutoApproveUnderUsd || 0);

        let baseText = 'Los pagos automáticos están desactivados.';
        if (enabled) {
            if (mode === 'deny_all') {
                baseText = 'Toda acción con gasto quedará bloqueada por política.';
            } else if (mode === 'ask_always') {
                baseText = 'Todo gasto requerirá aprobación manual del usuario.';
            } else if (mode === 'ask_above_x') {
                baseText = `Los pagos pedirán aprobación solo si superan $${askAbove || 0}; por debajo de ese umbral podrán avanzar con critic pero sin aprobación manual.`;
            } else if (mode === 'auto_approve_under_x') {
                baseText = `Los pagos de hasta $${autoApproveUnder || 0} podrán auto-aprobarse; por encima de ese monto se pedirá aprobación manual.`;
            }
        }
        this.paymentsMeta.textContent = `${statusMessage ? `${statusMessage} ` : ''}${baseText}`.trim();
    }

    _renderPaymentAccountsMeta(statusMessage = '') {
        if (!this.paymentsAccountsMeta) return;
        const defaultAccountId = String(this.paymentsDefaultAccountInput?.value || this.currentDefaultPaymentAccountId || '').trim();
        let accounts = [];
        try {
            accounts = this._parseJsonConfig(this.paymentsAccountsInput?.value || '[]', []);
            if (!Array.isArray(accounts)) {
                throw new Error('Las cuentas de pago deben ser una lista JSON.');
            }
        } catch (err) {
            this.paymentsAccountsMeta.textContent = `JSON inválido en cuentas de pago: ${err.message || err}`;
            return;
        }
        const enabledCount = accounts.filter((item) => item && typeof item === 'object' && item.enabled !== false).length;
        const baseText = accounts.length
            ? `Hay ${accounts.length} cuenta(s) o tarjeta(s) registradas; ${enabledCount} activas. Cuenta por defecto: ${defaultAccountId || 'ninguna'}.`
            : 'No hay cuentas de pago registradas todavía.';
        this.paymentsAccountsMeta.textContent = `${statusMessage ? `${statusMessage} ` : ''}${baseText}`.trim();
    }

    _applyProviderChange() {
        ws.sendConfig('model_router', 'default_provider', this.currentProvider);
        // Guardar también el modelo: puede haberse reseteado al primer modelo del nuevo provider
        // (esto pasa cuando _updateModelOptions() detecta que el modelo anterior no existe aquí)
        ws.sendConfig('model_router', 'default_model', this.currentModel);
        this.updateModelLabel();
        // Verificar soporte RT con el nuevo provider+modelo
        ws.checkRealtimeAvailable(this.currentProvider, this.currentModel);
    }

    _applyModelChange() {
        ws.sendConfig('model_router', 'default_model', this.currentModel);
        this.updateModelLabel();
        ws.checkRealtimeAvailable(this.currentProvider, this.currentModel);
    }

    _toggleGoogleBackendGroup() {
        const group = document.getElementById('google-backend-group');
        if (group) {
            group.style.display = this.currentProvider === 'google' ? '' : 'none';
        }
    }

    async _syncGoogleBackendConfig() {
        try {
            const resp = await fetch(`${BACKEND_API}/providers/google/backend`);
            if (!resp.ok) return;
            const data = await resp.json();

            const backendSelect = document.getElementById('select-google-backend');
            const projectInput = document.getElementById('input-google-project-id');
            const locationSelect = document.getElementById('select-google-location');
            const credentialsInput = document.getElementById('input-google-credentials-file');
            const vertexConfig = document.getElementById('google-vertex-config');
            const statusEl = document.getElementById('google-backend-status');

            if (backendSelect) backendSelect.value = data.backend || 'ai_studio';
            if (projectInput) projectInput.value = data.project_id || '';
            if (locationSelect) locationSelect.value = data.location || 'us-central1';
            if (credentialsInput) credentialsInput.value = data.credentials_file || '';
            if (vertexConfig) vertexConfig.style.display = data.backend === 'vertex_ai' ? '' : 'none';

            if (statusEl) {
                if (data.backend === 'vertex_ai') {
                    statusEl.textContent = data.project_id
                        ? `Vertex AI: ${data.project_id} (${data.location})`
                        : 'Vertex AI: falta Project ID';
                    statusEl.className = data.project_id ? 'api-key-status set' : 'api-key-status unset';
                } else {
                    statusEl.textContent = 'AI Studio (API Key)';
                    statusEl.className = 'api-key-status';
                }
            }

            this._toggleGoogleBackendGroup();
        } catch (err) {
            // Backend not ready
        }
    }

    async _saveGoogleBackendConfig() {
        const backendSelect = document.getElementById('select-google-backend');
        const projectInput = document.getElementById('input-google-project-id');
        const locationSelect = document.getElementById('select-google-location');
        const credentialsInput = document.getElementById('input-google-credentials-file');
        const statusEl = document.getElementById('google-backend-status');
        const saveBtn = document.getElementById('btn-save-google-backend');

        const payload = {
            backend: backendSelect?.value || 'ai_studio',
            project_id: projectInput?.value?.trim() || '',
            location: locationSelect?.value || 'us-central1',
            credentials_file: credentialsInput?.value?.trim() || '',
        };

        try {
            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.textContent = 'Conectando...';
            }

            const resp = await fetch(`${BACKEND_API}/providers/google/backend`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();

            if (statusEl) {
                if (data.success) {
                    if (data.backend === 'vertex_ai') {
                        statusEl.textContent = `Vertex AI conectado: ${data.project_id} (${data.location})`;
                    } else {
                        statusEl.textContent = 'AI Studio (API Key) configurado';
                    }
                    statusEl.className = 'api-key-status set';
                } else {
                    statusEl.textContent = `Error: ${data.error || 'desconocido'}`;
                    statusEl.className = 'api-key-status error';
                }
            }
        } catch (err) {
            if (statusEl) {
                statusEl.textContent = `Error de red: ${err.message}`;
                statusEl.className = 'api-key-status error';
            }
        } finally {
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Guardar y conectar';
            }
        }
    }

    async _syncMonitors() {
        const select = document.getElementById('select-target-monitor');
        const meta = document.getElementById('monitor-meta');
        if (!select) return;
        try {
            const resp = await fetch(`${BACKEND_API}/monitors`);
            if (!resp.ok) return;
            const data = await resp.json();
            const monitors = data.monitors || [];
            const target = data.target_monitor || 0;

            select.innerHTML = '<option value="0">Todos los monitores (combinado)</option>';
            monitors.forEach((m) => {
                const opt = document.createElement('option');
                opt.value = String(m.index);
                opt.textContent = `Monitor ${m.index}${m.primary ? ' (principal)' : ''} — ${m.width}x${m.height}`;
                select.appendChild(opt);
            });
            select.value = String(target);

            if (meta) {
                meta.textContent = monitors.length > 0
                    ? `${monitors.length} monitor(es) detectados. Activo: ${target === 0 ? 'todos' : 'monitor ' + target}.`
                    : 'No se detectaron monitores.';
            }
        } catch (err) {
            if (meta) meta.textContent = 'Error detectando monitores.';
        }
    }

    async _saveTargetMonitor(monitor) {
        const meta = document.getElementById('monitor-meta');
        try {
            const resp = await fetch(`${BACKEND_API}/monitors/target`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ monitor }),
            });
            const data = await resp.json();
            if (meta) {
                meta.textContent = data.success
                    ? `Monitor objetivo cambiado a: ${monitor === 0 ? 'todos' : 'monitor ' + monitor}`
                    : 'Error guardando monitor.';
            }
        } catch (err) {
            if (meta) meta.textContent = 'Error de conexion.';
        }
    }

    updateModelLabel() {
        document.getElementById('model-label').textContent = `${this.currentProvider}/${this.currentModel}`;
    }

    updateModeLabel(modeName) {
        const modeLabel = document.getElementById('mode-label');
        if (modeLabel) modeLabel.textContent = `modo: ${modeName}`;
    }

    async refreshModesFromBackend() {
        await this._syncModesFromBackend();
    }

    _renderModes() {
        if (!this.modeSelect || !Array.isArray(this.availableModes) || this.availableModes.length === 0) return;

        this.modeSelect.innerHTML = '';
        this.availableModes.forEach((mode) => {
            const opt = document.createElement('option');
            opt.value = mode.key;
            opt.textContent = mode.is_custom ? `${mode.name} [custom]` : mode.name;
            if (mode.key === this.currentMode) opt.selected = true;
            this.modeSelect.appendChild(opt);
        });
    }

    _renderModeSummary(modeData) {
        if (!this.modeSummary) return;

        const selectedMode = this.availableModes.find((mode) => mode.key === this.currentMode);
        const description = modeData.current_mode_description || modeData.description || selectedMode?.description || '';
        const allowed = modeData.allowed_capabilities || selectedMode?.allowed_capabilities || [];
        const restricted = modeData.restricted_capabilities || selectedMode?.restricted_capabilities || [];
        const scopeNote = modeData.requires_scope_confirmation || selectedMode?.requires_scope_confirmation
            ? ' Requiere confirmacion de scope.'
            : '';

        this.modeSummary.textContent = `Permite: ${allowed.join(', ') || 'sin definir'}. Bloquea o limita: ${restricted.join(', ') || 'sin restricciones'}.${
            description ? ` ${description}.` : ''
        }${scopeNote}`;
    }

    _renderCustomModeEditor() {
        const selectedMode = this.availableModes.find((mode) => mode.key === this.currentMode);
        if (!selectedMode) return;

        if (selectedMode.is_custom) {
            this.customModeKeyInput.value = selectedMode.key || '';
            this.customModeNameInput.value = selectedMode.name || '';
            this.customModeIconInput.value = selectedMode.icon || '';
            this.customModeDescriptionInput.value = selectedMode.description || '';
            this.customModeBehaviorInput.value = selectedMode.behavior_prompt || '';
            this.customModeSystemInput.value = selectedMode.system_prompt || '';
            this.customModeAllowedInput.value = (selectedMode.allowed_capabilities || []).join(', ');
            this.customModeRestrictedInput.value = (selectedMode.restricted_capabilities || []).join(', ');
            this.customModeScopeCheckbox.checked = !!selectedMode.requires_scope_confirmation;
            this.customModeMeta.textContent = `Editando modo custom activo: ${selectedMode.key}`;
            this.customModeDeleteBtn.disabled = false;
            return;
        }

        this.customModeKeyInput.value = '';
        this.customModeNameInput.value = selectedMode.name || '';
        this.customModeIconInput.value = selectedMode.icon || '';
        this.customModeDescriptionInput.value = selectedMode.description || '';
        this.customModeBehaviorInput.value = selectedMode.behavior_prompt || '';
        this.customModeSystemInput.value = selectedMode.system_prompt || '';
        this.customModeAllowedInput.value = (selectedMode.allowed_capabilities || []).join(', ');
        this.customModeRestrictedInput.value = (selectedMode.restricted_capabilities || []).join(', ');
        this.customModeScopeCheckbox.checked = !!selectedMode.requires_scope_confirmation;
        this.customModeMeta.textContent = 'Modo predefinido cargado como base. Para guardarlo, escribe una clave nueva.';
        this.customModeDeleteBtn.disabled = true;
    }

    _parseCapabilityList(rawValue) {
        return String(rawValue || '')
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean);
    }

    async _saveCustomMode() {
        const modeKey = this.customModeKeyInput.value.trim().toLowerCase();
        if (!modeKey) {
            this.customModeMeta.textContent = 'Define una clave para guardar el modo personalizado.';
            return;
        }

        try {
            const resp = await fetch(`${BACKEND_API}/modes/custom/${encodeURIComponent(modeKey)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: this.customModeNameInput.value.trim() || modeKey,
                    description: this.customModeDescriptionInput.value.trim(),
                    icon: this.customModeIconInput.value.trim(),
                    behavior_prompt: this.customModeBehaviorInput.value,
                    system_prompt: this.customModeSystemInput.value,
                    allowed_capabilities: this._parseCapabilityList(this.customModeAllowedInput.value),
                    restricted_capabilities: this._parseCapabilityList(this.customModeRestrictedInput.value),
                    requires_scope_confirmation: this.customModeScopeCheckbox.checked,
                }),
            });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                this.customModeMeta.textContent = `No se pudo guardar el modo: ${data.detail || data.error || 'error'}`;
                return;
            }

            this.currentMode = modeKey;
            await this._syncModesFromBackend();
            await this._applyModeChange();
            this.customModeMeta.textContent = `Modo custom guardado: ${modeKey}`;
        } catch (err) {
            console.error('Error guardando modo custom:', err);
            this.customModeMeta.textContent = 'Error de red guardando el modo custom.';
        }
    }

    async _deleteCustomMode() {
        const modeKey = this.customModeKeyInput.value.trim().toLowerCase();
        if (!modeKey) {
            this.customModeMeta.textContent = 'No hay clave de modo custom para borrar.';
            return;
        }

        try {
            const resp = await fetch(`${BACKEND_API}/modes/custom/${encodeURIComponent(modeKey)}`, {
                method: 'DELETE',
            });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                this.customModeMeta.textContent = `No se pudo borrar el modo: ${data.detail || data.error || 'error'}`;
                return;
            }

            this.currentMode = 'normal';
            await this._syncModesFromBackend();
            this.customModeMeta.textContent = `Modo custom eliminado: ${modeKey}`;
        } catch (err) {
            console.error('Error borrando modo custom:', err);
            this.customModeMeta.textContent = 'Error de red borrando el modo custom.';
        }
    }

    async _syncPromptTemplates() {
        try {
            const resp = await fetch(`${BACKEND_API}/prompts`);
            if (!resp.ok) return;
            const data = await resp.json();
            const promptData = data?.data || {};
            this.availablePromptTemplates = [
                ...(promptData.core || []),
                ...(promptData.modes || []),
            ];
            this._renderPromptOptions();
        } catch (err) {
            console.error('Error cargando prompts:', err);
        }
    }

    _renderPromptOptions() {
        if (!this.promptSelect) return;
        this.promptSelect.innerHTML = '';

        if (!Array.isArray(this.availablePromptTemplates) || this.availablePromptTemplates.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No hay prompts disponibles';
            this.promptSelect.appendChild(opt);
            return;
        }

        this.availablePromptTemplates.forEach((item, index) => {
            const opt = document.createElement('option');
            opt.value = item.key;
            opt.textContent = item.label || item.key;
            if (index === 0) opt.selected = true;
            this.promptSelect.appendChild(opt);
        });
        this._renderSelectedPrompt();
    }

    _renderSelectedPrompt() {
        if (!this.promptSelect || !this.promptInput || !this.promptMeta) return;
        const selected = this.availablePromptTemplates.find((item) => item.key === this.promptSelect.value)
            || this.availablePromptTemplates[0];
        if (!selected) {
            this.promptInput.value = '';
            this.promptMeta.textContent = '';
            return;
        }
        this.promptSelect.value = selected.key;
        this.promptInput.value = selected.content || '';
        const sourceLabel = selected.source || 'desconocido';
        const modeLabel = selected.mode_key ? ` | modo: ${selected.mode_key}` : '';
        this.promptMeta.textContent = `Fuente activa: ${sourceLabel}${modeLabel}. Los placeholders dinámicos como {available_modes} se respetan si los dejas en el texto.`;
    }

    async _savePromptTemplate() {
        const key = this.promptSelect?.value;
        if (!key) return;
        try {
            const resp = await fetch(`${BACKEND_API}/prompts`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    key,
                    content: this.promptInput.value,
                }),
            });
            const data = await resp.json();
            if (resp.ok && data.success) {
                await this._syncPromptTemplates();
            }
        } catch (err) {
            console.error('Error guardando prompt:', err);
        }
    }

    async _resetPromptTemplate() {
        const key = this.promptSelect?.value;
        if (!key) return;
        try {
            const resp = await fetch(`${BACKEND_API}/prompts/${encodeURIComponent(key)}`, {
                method: 'DELETE',
            });
            const data = await resp.json();
            if (resp.ok && data.success) {
                await this._syncPromptTemplates();
            }
        } catch (err) {
            console.error('Error reseteando prompt:', err);
        }
    }

    // ── Budget fields toggle ──────────────────────────────────
    _toggleBudgetFields() {
        const budgetGroup = this.budgetEnabledCheckbox?.closest('.setting-group');
        if (!budgetGroup) return;
        if (this.budgetEnabledCheckbox.checked) {
            budgetGroup.classList.remove('disabled-group');
        } else {
            budgetGroup.classList.add('disabled-group');
        }
    }

    // ── JSON textarea real-time validation ────────────────────
    _setupJsonValidation(textarea) {
        if (!textarea) return;
        let timer = null;
        textarea.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                const val = (textarea.value || '').trim();
                if (!val) {
                    textarea.classList.remove('json-invalid');
                    return;
                }
                try {
                    JSON.parse(val);
                    textarea.classList.remove('json-invalid');
                } catch {
                    textarea.classList.add('json-invalid');
                }
            }, 400);
        });
    }

    // ── Model Assignments UI ──────────────────────────────────
    async _syncModelAssignments() {
        try {
            const resp = await fetch(`${BACKEND_API}/config/model_assignments`);
            if (resp.ok) {
                const data = await resp.json();
                this.currentModelAssignments = data?.data?.model_assignments || {};
            }
        } catch (err) {
            // Backend no listo
        }
        this._renderModelAssignments();
    }

    _renderModelAssignments() {
        if (!this.modelAssignmentsContainer) return;
        this.modelAssignmentsContainer.innerHTML = '';

        const TASK_TYPES = [
            { key: 'programacion', label: 'Programación' },
            { key: 'frontend', label: 'Frontend' },
            { key: 'lectura', label: 'Lectura' },
            { key: 'creatividad', label: 'Creatividad' },
            { key: 'matematicas', label: 'Matemáticas' },
            { key: 'sin_censura', label: 'Sin Censura' },
            { key: 'computer_use', label: 'Computer Use (UI)' },
            { key: 'analisis', label: 'Análisis' },
            { key: 'general', label: 'General' },
        ];

        const providers = Object.keys(MODEL_OPTIONS).filter((p) => MODEL_OPTIONS[p].length > 0);

        for (const taskType of TASK_TYPES) {
            const currentValue = this.currentModelAssignments[taskType.key] || '';
            const [currentProvider, currentModel] = currentValue.includes(':') ? currentValue.split(':', 2) : ['', ''];

            const row = document.createElement('div');
            row.className = 'model-assignment-row';
            row.dataset.taskType = taskType.key;

            const label = document.createElement('label');
            label.textContent = taskType.label;

            const providerSelect = document.createElement('select');
            providerSelect.className = 'setting-select';
            providerSelect.dataset.role = 'provider';
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = '(heredar default)';
            providerSelect.appendChild(defaultOpt);
            for (const prov of providers) {
                const opt = document.createElement('option');
                opt.value = prov;
                opt.textContent = PROVIDER_LABELS[prov] || prov;
                if (prov === currentProvider) opt.selected = true;
                providerSelect.appendChild(opt);
            }

            const modelSelect = document.createElement('select');
            modelSelect.className = 'setting-select';
            modelSelect.dataset.role = 'model';

            const populateModels = (prov, selected) => {
                modelSelect.innerHTML = '';
                const defOpt = document.createElement('option');
                defOpt.value = '';
                defOpt.textContent = '(auto)';
                modelSelect.appendChild(defOpt);
                const models = MODEL_OPTIONS[prov] || [];
                for (const m of models) {
                    const opt = document.createElement('option');
                    opt.value = m;
                    opt.textContent = m;
                    if (m === selected) opt.selected = true;
                    modelSelect.appendChild(opt);
                }
            };

            populateModels(currentProvider, currentModel);

            providerSelect.addEventListener('change', () => {
                populateModels(providerSelect.value, '');
            });

            row.appendChild(label);
            row.appendChild(providerSelect);
            row.appendChild(modelSelect);
            this.modelAssignmentsContainer.appendChild(row);
        }
    }

    async _saveModelAssignments() {
        const assignments = {};
        this.modelAssignmentsContainer?.querySelectorAll('.model-assignment-row').forEach((row) => {
            const taskType = row.dataset.taskType;
            const provider = row.querySelector('[data-role="provider"]')?.value || '';
            const model = row.querySelector('[data-role="model"]')?.value || '';
            assignments[taskType] = provider && model ? `${provider}:${model}` : '';
        });
        // Save each assignment individually
        for (const [taskType, value] of Object.entries(assignments)) {
            await this._saveConfigValue('model_assignments', taskType, value);
        }
        this.currentModelAssignments = assignments;
        if (this.modelAssignmentsMeta) {
            const configured = Object.entries(assignments).filter(([, v]) => v).length;
            this.modelAssignmentsMeta.textContent = `Asignaciones guardadas. ${configured} tipo(s) con modelo específico.`;
        }
    }

    // ── Computer Use Config ─────────────────────────────────
    async _loadComputerUseConfig() {
        try {
            const resp = await fetch(`${BACKEND_API}/config`);
            if (!resp.ok) return;
            const data = await resp.json();
            const cu = data?.data?.computer_use || data?.computer_use || {};
            const cbEnabled = document.getElementById('cb-computer-use-enabled');
            const selProvider = document.getElementById('select-computer-use-provider');
            const inputMaxIter = document.getElementById('input-computer-use-max-iter');
            const inputTimeout = document.getElementById('input-computer-use-timeout');
            const inputDelay = document.getElementById('input-computer-use-delay');
            if (cbEnabled) cbEnabled.checked = cu.enabled !== false;
            const provider = String(cu.provider || 'google').toLowerCase();
            if (selProvider) {
                selProvider.innerHTML = '';
                for (const p of Object.keys(COMPUTER_USE_MODELS)) {
                    const opt = document.createElement('option');
                    opt.value = p;
                    opt.textContent = COMPUTER_USE_PROVIDER_LABELS[p] || p;
                    if (p === provider) opt.selected = true;
                    selProvider.appendChild(opt);
                }
                selProvider.onchange = () => this._populateComputerUseModels(selProvider.value, '');
            }
            this._populateComputerUseModels(provider, cu.model || '');
            if (inputMaxIter && cu.max_iterations) inputMaxIter.value = cu.max_iterations;
            if (inputTimeout && cu.timeout_seconds) inputTimeout.value = cu.timeout_seconds;
            if (inputDelay && cu.stabilization_delay_seconds) inputDelay.value = cu.stabilization_delay_seconds;
        } catch (err) {
            console.warn('No se pudo cargar config computer_use:', err);
        }
    }

    _populateComputerUseModels(provider, selected) {
        const selModel = document.getElementById('select-computer-use-model');
        if (!selModel) return;
        selModel.innerHTML = '';
        const models = [...(COMPUTER_USE_MODELS[provider] || [])];
        if (selected && !models.includes(selected)) models.unshift(selected);
        for (const m of models) {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            if (m === selected) opt.selected = true;
            selModel.appendChild(opt);
        }
        if (!selModel.value && selModel.options.length) selModel.selectedIndex = 0;
    }

    async _saveComputerUseConfig() {
        const enabled = document.getElementById('cb-computer-use-enabled')?.checked ?? true;
        const provider = document.getElementById('select-computer-use-provider')?.value || 'google';
        const model = document.getElementById('select-computer-use-model')?.value || 'gemini-2.5-computer-use-preview-10-2025';
        const maxIter = parseInt(document.getElementById('input-computer-use-max-iter')?.value || '30', 10);
        const timeout = parseInt(document.getElementById('input-computer-use-timeout')?.value || '180', 10);
        const delay = parseFloat(document.getElementById('input-computer-use-delay')?.value || '3');

        await this._saveConfigValue('computer_use', 'enabled', enabled);
        await this._saveConfigValue('computer_use', 'provider', provider);
        await this._saveConfigValue('computer_use', 'model', model);
        await this._saveConfigValue('computer_use', 'max_iterations', maxIter);
        await this._saveConfigValue('computer_use', 'timeout_seconds', timeout);
        await this._saveConfigValue('computer_use', 'stabilization_delay_seconds', delay);

        const btn = document.getElementById('btn-save-computer-use');
        if (btn) {
            const orig = btn.textContent;
            btn.textContent = 'Guardado ✓';
            setTimeout(() => { btn.textContent = orig; }, 2000);
        }
    }

    // ── Crews UI ──────────────────────────────────────────────
    async _syncCrews() {
        try {
            const resp = await fetch(`${BACKEND_API}/crews`);
            if (resp.ok) {
                const data = await resp.json();
                this.currentCrews = Array.isArray(data?.crews) ? data.crews : [];
            }
        } catch (err) {
            // Backend no listo — try config fallback
            try {
                const resp = await fetch(`${BACKEND_API}/config/crews`);
                if (resp.ok) {
                    const data = await resp.json();
                    this.currentCrews = Array.isArray(data?.data?.crews?.definitions) ? data.data.crews.definitions : [];
                }
            } catch (err2) {
                // Backend no listo
            }
        }
        this._renderCrews();
    }

    _renderCrews() {
        if (!this.crewsList) return;
        this.crewsList.innerHTML = '';

        for (const crew of this.currentCrews) {
            const card = document.createElement('div');
            card.className = 'crew-card';
            card.dataset.crewId = crew.id;

            const row = document.createElement('div');
            row.className = 'crew-card-row';

            const expand = document.createElement('span');
            expand.className = 'crew-card-expand';
            expand.textContent = '▶';

            const name = document.createElement('span');
            name.className = 'crew-card-name';
            name.textContent = crew.name || crew.id;

            const badge = document.createElement('span');
            badge.className = 'crew-card-badge';
            badge.textContent = `${crew.process || 'sequential'} · ${(crew.agents || []).length} roles`;

            const actions = document.createElement('div');
            actions.className = 'crew-card-actions';

            const editBtn = document.createElement('button');
            editBtn.className = 'crew-card-btn';
            editBtn.title = 'Editar';
            editBtn.innerHTML = SETTINGS_ICONS.edit;
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._editCrew(crew);
            });

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'crew-card-btn delete';
            deleteBtn.title = 'Eliminar';
            deleteBtn.innerHTML = SETTINGS_ICONS.close;
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._deleteCrew(crew.id);
            });

            actions.appendChild(editBtn);
            actions.appendChild(deleteBtn);

            row.appendChild(expand);
            row.appendChild(name);
            row.appendChild(badge);
            row.appendChild(actions);

            // Details
            const details = document.createElement('div');
            details.className = 'crew-card-details';

            if (crew.process === 'hierarchical' && crew.manager_model) {
                const mgrRow = document.createElement('div');
                mgrRow.style.cssText = 'margin-bottom:6px; color:var(--text-muted); font-size:11px;';
                mgrRow.textContent = `Manager: ${crew.manager_model}`;
                details.appendChild(mgrRow);
            }

            if (Array.isArray(crew.agents) && crew.agents.length > 0) {
                const table = document.createElement('table');
                table.className = 'crew-roles-table';
                table.innerHTML = '<thead><tr><th>Rol</th><th>Modelo</th><th>Delegar</th><th>Iters</th></tr></thead>';
                const tbody = document.createElement('tbody');
                for (const agent of crew.agents) {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `<td>${this._escapeHtml(agent.role || '?')}</td><td>${this._escapeHtml(agent.model ? `${agent.provider || ''}:${agent.model}` : '(default)')}</td><td>${agent.can_delegate ? '✓' : '—'}</td><td>${agent.max_iterations || 10}</td>`;
                    tbody.appendChild(tr);
                }
                table.appendChild(tbody);
                details.appendChild(table);
            }

            row.addEventListener('click', () => card.classList.toggle('expanded'));

            card.appendChild(row);
            card.appendChild(details);
            this.crewsList.appendChild(card);
        }

        if (this.crewsMeta) {
            this.crewsMeta.textContent = this.currentCrews.length
                ? `${this.currentCrews.length} equipo(s) configurado(s).`
                : '';
        }
    }

    _addCrewRoleEntry(defaults = {}) {
        if (!this.crewRolesContainer) return;
        const entry = document.createElement('div');
        entry.className = 'crew-role-entry';

        entry.innerHTML = `
            <input class="setting-input" data-field="role" type="text" placeholder="Rol (ej: Backend Dev)" value="${this._escapeHtml(defaults.role || '')}">
            <input class="setting-input" data-field="goal" type="text" placeholder="Meta/objetivo" value="${this._escapeHtml(defaults.goal || '')}">
            <input class="setting-input" data-field="model" type="text" placeholder="provider:model" value="${this._escapeHtml(defaults.model ? `${defaults.provider || ''}:${defaults.model}` : '')}" style="min-width:140px;">
            <select class="setting-select" data-field="can_delegate">
                <option value="false" ${!defaults.can_delegate ? 'selected' : ''}>No delega</option>
                <option value="true" ${defaults.can_delegate ? 'selected' : ''}>Puede delegar</option>
            </select>
            <button class="crew-role-remove" title="Quitar">${SETTINGS_ICONS.close}</button>
        `;

        entry.querySelector('.crew-role-remove').addEventListener('click', () => entry.remove());
        this.crewRolesContainer.appendChild(entry);
    }

    _applyCrewTemplate(templateId) {
        const templates = {
            'dev-fullstack': {
                name: 'Equipo Dev Full-Stack',
                process: 'sequential',
                agents: [
                    { role: 'Arquitecto', goal: 'Diseñar la estructura del proyecto', can_delegate: false },
                    { role: 'Backend Dev', goal: 'Implementar la lógica del servidor y APIs', can_delegate: false },
                    { role: 'Frontend Dev', goal: 'Implementar la interfaz de usuario', can_delegate: false },
                    { role: 'Tester', goal: 'Escribir y ejecutar tests automatizados', can_delegate: false },
                ],
            },
            'analisis': {
                name: 'Equipo de Análisis',
                process: 'sequential',
                agents: [
                    { role: 'Investigador', goal: 'Recopilar información relevante', can_delegate: false },
                    { role: 'Analista', goal: 'Analizar datos y extraer conclusiones', can_delegate: false },
                    { role: 'Escritor', goal: 'Redactar el reporte final', can_delegate: false },
                ],
            },
            'diseno': {
                name: 'Equipo de Diseño',
                process: 'sequential',
                agents: [
                    { role: 'UX Designer', goal: 'Diseñar la experiencia de usuario y wireframes', can_delegate: false },
                    { role: 'Frontend Dev', goal: 'Implementar el diseño en código', can_delegate: false },
                ],
            },
        };

        const tpl = templates[templateId];
        if (!tpl) return;

        if (this.crewNameInput) this.crewNameInput.value = tpl.name;
        if (this.crewProcessSelect) this.crewProcessSelect.value = tpl.process;
        if (this.crewRolesContainer) this.crewRolesContainer.innerHTML = '';
        for (const agent of tpl.agents) {
            this._addCrewRoleEntry(agent);
        }
        if (this.crewFormMeta) this.crewFormMeta.textContent = `Template "${tpl.name}" aplicado. Personalízalo y guárdalo.`;
    }

    async _saveNewCrew() {
        const name = (this.crewNameInput?.value || '').trim();
        if (!name) {
            if (this.crewFormMeta) this.crewFormMeta.textContent = 'El nombre del equipo es obligatorio.';
            return;
        }
        const process = this.crewProcessSelect?.value || 'sequential';
        const managerModel = process === 'hierarchical' ? (this.crewManagerModelInput?.value || '').trim() : '';

        const agents = [];
        this.crewRolesContainer?.querySelectorAll('.crew-role-entry').forEach((entry) => {
            const role = (entry.querySelector('[data-field="role"]')?.value || '').trim();
            if (!role) return;
            const goal = (entry.querySelector('[data-field="goal"]')?.value || '').trim();
            const modelRaw = (entry.querySelector('[data-field="model"]')?.value || '').trim();
            const canDelegate = entry.querySelector('[data-field="can_delegate"]')?.value === 'true';
            const [provider, model] = modelRaw.includes(':') ? modelRaw.split(':', 2) : ['', ''];
            agents.push({ role, goal, provider: provider || null, model: model || null, can_delegate: canDelegate, max_iterations: 10 });
        });

        if (agents.length === 0) {
            if (this.crewFormMeta) this.crewFormMeta.textContent = 'Agrega al menos un rol al equipo.';
            return;
        }

        const crew = {
            id: name.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
            name,
            process,
            manager_model: managerModel || null,
            agents,
        };

        try {
            const resp = await fetch(`${BACKEND_API}/crews`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(crew),
            });
            if (resp.ok) {
                await this._syncCrews();
                // Clear form
                if (this.crewNameInput) this.crewNameInput.value = '';
                if (this.crewRolesContainer) this.crewRolesContainer.innerHTML = '';
                if (this.crewFormMeta) this.crewFormMeta.textContent = `Equipo "${name}" guardado.`;
                return;
            }
        } catch (err) {
            // Fallback: save to config directly
        }

        // Fallback: save to config if /crews endpoint not available
        this.currentCrews.push(crew);
        await this._saveConfigValue('crews', 'definitions', this.currentCrews);
        this._renderCrews();
        if (this.crewNameInput) this.crewNameInput.value = '';
        if (this.crewRolesContainer) this.crewRolesContainer.innerHTML = '';
        if (this.crewFormMeta) this.crewFormMeta.textContent = `Equipo "${name}" guardado en configuración.`;
    }

    async _deleteCrew(crewId) {
        try {
            const resp = await fetch(`${BACKEND_API}/crews/${encodeURIComponent(crewId)}`, { method: 'DELETE' });
            if (resp.ok) {
                await this._syncCrews();
                return;
            }
        } catch (err) {
            // fallback
        }
        // Fallback: remove from config
        this.currentCrews = this.currentCrews.filter((c) => c.id !== crewId);
        await this._saveConfigValue('crews', 'definitions', this.currentCrews);
        this._renderCrews();
    }

    _editCrew(crew) {
        if (this.crewNameInput) this.crewNameInput.value = crew.name || '';
        if (this.crewProcessSelect) this.crewProcessSelect.value = crew.process || 'sequential';
        if (this.crewManagerModelInput) this.crewManagerModelInput.value = crew.manager_model || '';
        const managerSection = document.getElementById('crew-manager-section');
        if (managerSection) managerSection.style.display = crew.process === 'hierarchical' ? '' : 'none';
        if (this.crewRolesContainer) this.crewRolesContainer.innerHTML = '';
        for (const agent of (crew.agents || [])) {
            this._addCrewRoleEntry(agent);
        }
        // Remove old crew so saving creates updated version
        this.currentCrews = this.currentCrews.filter((c) => c.id !== crew.id);
        if (this.crewFormMeta) this.crewFormMeta.textContent = `Editando "${crew.name}". Modifica y guarda.`;
        this._switchPage('crews');
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}

const settingsManager = new SettingsManager();
window.settingsManager = settingsManager;
