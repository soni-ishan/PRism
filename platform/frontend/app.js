/**
 * PRism Setup Wizard — app.js
 *
 * Manages the 3-step wizard state, handles OAuth redirect flows (reads query
 * params injected by the backend callbacks), and calls the platform API.
 */

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  currentStep: 1,
  github: {
    connected: false,
    token: null,        // OAuth access token or null (App flow uses backend)
    installationId: null,
    repos: [],
  },
  azure: {
    connected: false,
    token: null,
    subscriptionId: null,
    workspaceId: null,
    workspaceName: null,
    customerId: null,
    envVars: null,
  },
};

// ── Initialisation ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  readOAuthCallbackParams();
  restoreState();
  renderStep(state.currentStep);
  checkOrchestratorHealth();
});

/**
 * Read any query parameters injected by the backend OAuth callbacks.
 * The backend redirects to /?github_connected=true&... or /?azure_connected=true&...
 * We consume the params, update state, then clean up the URL.
 */
function readOAuthCallbackParams() {
  const params = new URLSearchParams(window.location.search);

  if (params.get('github_connected') === 'true') {
    state.github.connected = true;
    const token = params.get('github_token');
    const installId = params.get('installation_id');
    if (token) state.github.token = token;
    if (installId) state.github.installationId = installId;
    // After GitHub connected, jump to step 2
    state.currentStep = 2;
  }

  if (params.get('azure_connected') === 'true') {
    state.azure.connected = false; // Connected to auth, but workspace not yet picked
    const token = params.get('azure_token');
    if (token) state.azure.token = token;
    // Show subscription/workspace picker
    state.currentStep = 2;
  }

  if (params.toString()) {
    // Clean up the URL so tokens don't sit in browser history
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

/** Persist lightweight state to sessionStorage so page refreshes survive. */
function saveState() {
  try {
    sessionStorage.setItem('prism_setup_state', JSON.stringify({
      currentStep: state.currentStep,
      github: { connected: state.github.connected, installationId: state.github.installationId },
      azure: {
        connected: state.azure.connected,
        subscriptionId: state.azure.subscriptionId,
        workspaceId: state.azure.workspaceId,
        workspaceName: state.azure.workspaceName,
        customerId: state.azure.customerId,
        envVars: state.azure.envVars,
      },
    }));
  } catch (_) { /* SessionStorage not available */ }
}

function restoreState() {
  try {
    const saved = sessionStorage.getItem('prism_setup_state');
    if (!saved) return;
    const parsed = JSON.parse(saved);
    // Merge — but don't override tokens already set from query params
    if (!state.github.connected && parsed.github?.connected) {
      state.github.connected = parsed.github.connected;
      state.github.installationId = parsed.github.installationId;
    }
    if (parsed.azure?.connected) {
      state.azure.connected = parsed.azure.connected;
      state.azure.subscriptionId = parsed.azure.subscriptionId;
      state.azure.workspaceId = parsed.azure.workspaceId;
      state.azure.workspaceName = parsed.azure.workspaceName;
      state.azure.customerId = parsed.azure.customerId;
      state.azure.envVars = parsed.azure.envVars;
    }
    if (!state.currentStep || state.currentStep === 1) {
      state.currentStep = parsed.currentStep || 1;
    }
  } catch (_) { /* Ignore */ }
}

// ── Step navigation ────────────────────────────────────────────────────────
function goToStep(n) {
  state.currentStep = n;
  renderStep(n);
  saveState();
}

function renderStep(n) {
  // Hide all steps
  document.querySelectorAll('.wizard-step').forEach(el => el.classList.add('hidden'));
  // Show current step
  const el = document.getElementById(`step-${n}`);
  if (el) el.classList.remove('hidden');

  // Update pill states
  for (let i = 1; i <= 3; i++) {
    const pill = document.getElementById(`pill-${i}`);
    if (!pill) continue;
    pill.classList.remove('active', 'done');
    if (i < n) pill.classList.add('done');
    if (i === n) pill.classList.add('active');
  }

  // Update step icons
  setStepIcon(1, state.github.connected ? '✅' : '1');
  setStepIcon(2, state.azure.connected  ? '✅' : '2');
  setStepIcon(3, '3');

  // Progress bar: step 1 = 16%, step 2 = 50%, step 3 = 100%
  const pct = { 1: 16, 2: 50, 3: 100 };
  document.getElementById('progressBar').style.width = (pct[n] || 0) + '%';

  // Render step-specific content
  if (n === 1) renderStep1();
  if (n === 2) renderStep2();
  if (n === 3) renderStep3();
}

function setStepIcon(n, icon) {
  const el = document.getElementById(`step-icon-${n}`);
  if (el) el.textContent = icon;
}

// ── Step 1: GitHub ─────────────────────────────────────────────────────────
function renderStep1() {
  const status = document.getElementById('step1-status');
  if (state.github.connected) {
    status.textContent = '✅';
    show('github-connected-panel');
    hide('github-connect-actions');

    const detail = document.getElementById('github-connected-detail');
    if (state.github.installationId) {
      detail.textContent = `App installation ID: ${state.github.installationId}`;
    } else if (state.github.token) {
      detail.textContent = 'OAuth token obtained. Use the form below to install the workflow.';
      show('github-workflow-section');
    }
  } else {
    status.textContent = '';
    hide('github-connected-panel');
    show('github-connect-actions');
  }
}

async function startGitHubConnect() {
  const btn = document.getElementById('btnGitHubInstall');
  btn.disabled = true;
  btn.textContent = 'Redirecting…';

  try {
    const resp = await fetch('/api/setup/github/install-url');
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    window.location.href = data.url;
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Install PRism on GitHub';
    alert(`Could not start GitHub connection: ${err.message}`);
  }
}

async function installWorkflow() {
  const owner = document.getElementById('inputOwner').value.trim();
  const repo  = document.getElementById('inputRepo').value.trim();
  const result = document.getElementById('workflow-install-result');

  if (!owner || !repo) {
    alert('Please enter both the owner and repository name.');
    return;
  }

  result.className = 'loading-tag';
  result.textContent = '⏳ Installing workflow…';
  show('workflow-install-result');

  try {
    const resp = await fetch('/api/setup/github/install-workflow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ owner, repo, token: state.github.token }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));

    result.className = 'success-banner';
    result.innerHTML = `✅ Workflow installed in <strong>${owner}/${repo}</strong>.
      ${data.commit_url ? `<br><a href="${data.commit_url}" target="_blank">View commit ↗</a>` : ''}`;
    goToStep(2);
  } catch (err) {
    result.className = 'error-tag';
    result.textContent = `❌ Failed: ${err.message}`;
  }
}

// ── Step 2: Azure ──────────────────────────────────────────────────────────
function renderStep2() {
  const status = document.getElementById('step2-status');
  if (state.azure.connected) {
    status.textContent = '✅';
    show('azure-connected-panel');
    hide('azure-auth-actions');
    hide('azure-picker-panel');

    document.getElementById('azure-connected-detail').textContent =
      `Workspace: ${state.azure.workspaceName || state.azure.workspaceId}`;
  } else if (state.azure.token) {
    // Token obtained but workspace not yet selected
    status.textContent = '';
    hide('azure-auth-actions');
    hide('azure-connected-panel');
    show('azure-picker-panel');
    loadSubscriptions();
  } else {
    status.textContent = '';
    show('azure-auth-actions');
    hide('azure-picker-panel');
    hide('azure-connected-panel');
  }
}

async function startAzureConnect() {
  const btn = document.getElementById('btnAzureSignIn');
  btn.disabled = true;
  btn.textContent = 'Redirecting…';

  try {
    const resp = await fetch('/api/setup/azure/auth-url');
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    window.location.href = data.url;
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Sign in with Azure';
    alert(`Could not start Azure connection: ${err.message}`);
  }
}

async function loadSubscriptions() {
  const select = document.getElementById('subSelect');
  select.innerHTML = '<option value="">— Loading… —</option>';

  try {
    const resp = await fetch(`/api/setup/azure/subscriptions?token=${encodeURIComponent(state.azure.token)}`);
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();

    select.innerHTML = '<option value="">— Select a subscription —</option>';
    for (const sub of data.subscriptions || []) {
      const opt = document.createElement('option');
      opt.value = sub.id;
      opt.textContent = sub.display_name;
      select.appendChild(opt);
    }
  } catch (err) {
    select.innerHTML = `<option value="">Error: ${err.message}</option>`;
  }
}

async function onSubscriptionChange() {
  const subId = document.getElementById('subSelect').value;
  const wsGroup = document.getElementById('ws-group');
  const btnConnectWs = document.getElementById('btnConnectWs');
  const wsSelect = document.getElementById('wsSelect');

  if (!subId) {
    wsGroup.style.display = 'none';
    btnConnectWs.style.display = 'none';
    return;
  }

  state.azure.subscriptionId = subId;
  wsGroup.style.display = '';
  wsSelect.innerHTML = '<option value="">— Loading workspaces… —</option>';

  try {
    const resp = await fetch(
      `/api/setup/azure/workspaces/${encodeURIComponent(subId)}?token=${encodeURIComponent(state.azure.token)}`
    );
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();

    wsSelect.innerHTML = '<option value="">— Select a workspace —</option>';
    for (const ws of data.workspaces || []) {
      const opt = document.createElement('option');
      opt.value = JSON.stringify(ws);
      opt.textContent = `${ws.name}  (${ws.resource_group})`;
      wsSelect.appendChild(opt);
    }
    btnConnectWs.style.display = '';
  } catch (err) {
    wsSelect.innerHTML = `<option value="">Error: ${err.message}</option>`;
  }
}

async function connectWorkspace() {
  const wsSelect = document.getElementById('wsSelect');
  const result = document.getElementById('workspace-connect-result');
  const raw = wsSelect.value;

  if (!raw) {
    alert('Please select a Log Analytics workspace.');
    return;
  }

  let ws;
  try { ws = JSON.parse(raw); } catch (_) { alert('Invalid workspace data.'); return; }

  result.className = 'loading-tag';
  result.textContent = '⏳ Connecting workspace…';
  show('workspace-connect-result');

  try {
    const resp = await fetch('/api/setup/azure/connect-workspace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subscription_id: state.azure.subscriptionId,
        workspace_id:   ws.id,
        workspace_name: ws.name,
        customer_id:    ws.customer_id || null,
        access_token:   state.azure.token,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));

    state.azure.connected    = true;
    state.azure.workspaceId  = ws.id;
    state.azure.workspaceName = ws.name;
    state.azure.customerId   = data.config?.customer_id;
    state.azure.envVars      = data.env_vars;

    saveState();
    renderStep2();
    goToStep(3);
  } catch (err) {
    result.className = 'error-tag';
    result.textContent = `❌ Failed: ${err.message}`;
  }
}

// ── Step 3: Verify ─────────────────────────────────────────────────────────
function renderStep3() {
  // GitHub summary
  const ghIcon   = document.getElementById('summary-github-icon');
  const ghDetail = document.getElementById('summary-github-detail');
  if (state.github.connected) {
    ghIcon.textContent = '✅';
    ghDetail.textContent = state.github.installationId
      ? `App installation #${state.github.installationId}`
      : 'OAuth token obtained';
  } else {
    ghIcon.textContent = '⚠️';
    ghDetail.textContent = 'Not yet connected — go back to Step 1';
  }

  // Azure summary
  const azIcon   = document.getElementById('summary-azure-icon');
  const azDetail = document.getElementById('summary-azure-detail');
  if (state.azure.connected) {
    azIcon.textContent = '✅';
    azDetail.textContent = state.azure.workspaceName || state.azure.workspaceId;
  } else {
    azIcon.textContent = '⚠️';
    azDetail.textContent = 'Not yet connected — go back to Step 2';
  }

  // Env vars panel
  if (state.azure.envVars) {
    document.getElementById('env-hint').style.display = '';
    const lines = Object.entries(state.azure.envVars)
      .map(([k, v]) => `${k}=${v}`)
      .join('\n');
    document.getElementById('env-output').textContent = lines;
  }
}

async function checkOrchestratorHealth() {
  const tag = document.getElementById('orchestrator-status');
  const link = document.getElementById('orchestrator-health-link');

  try {
    // Ask our own /health endpoint which reports the orchestrator URL
    const resp = await fetch('/health');
    if (!resp.ok) throw new Error(`/health returned ${resp.status}`);
    const data = await resp.json();

    const orchUrl = data.orchestrator_url || '';
    if (link) {
      link.href = `${orchUrl}/health`;
      link.textContent = `${orchUrl}/health ↗`;
    }

    // Ping the orchestrator health endpoint
    try {
      const orchResp = await fetch(`${orchUrl}/health`, { signal: AbortSignal.timeout(5000) });
      if (orchResp.ok) {
        tag.className = 'ok-tag';
        tag.textContent = `✅ Orchestrator is reachable at ${orchUrl}`;
      } else {
        tag.className = 'error-tag';
        tag.textContent = `⚠️ Orchestrator returned HTTP ${orchResp.status}`;
      }
    } catch (_) {
      tag.className = 'error-tag';
      tag.textContent = `⚠️ Could not reach orchestrator at ${orchUrl}`;
    }
  } catch (_) {
    tag.className = 'error-tag';
    tag.textContent = '⚠️ Could not load platform health info';
  }
}

function finishSetup() {
  document.getElementById('step3-status').textContent = '🎉';
  alert('🎉 PRism setup complete! Open a pull request in your connected repository to see it in action.');
  saveState();
}

// ── Helpers ────────────────────────────────────────────────────────────────
function show(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('hidden');
}

function hide(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}
