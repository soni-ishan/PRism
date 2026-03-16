/**
 * PRism — app.js
 *
 * Dashboard (registration list) and 3-step setup wizard logic.
 * Depends on auth.js being loaded first (for state, helpers, etc.)
 */

// ── Dashboard: Registrations ───────────────────────────────────────────────
async function loadRegistrations() {
  const listEl = document.getElementById('registrations-list');
  const emptyEl = document.getElementById('empty-state');

  try {
    const resp = await fetch('/api/registrations', { credentials: 'same-origin' });
    if (!resp.ok) throw new Error('Failed to load');
    const data = await resp.json();
    state.registrations = data.registrations || [];
  } catch (err) {
    listEl.innerHTML = `<div class="error-tag">Failed to load registrations: ${escapeHtml(err.message)}</div>`;
    hide('empty-state');
    return;
  }

  if (state.registrations.length === 0) {
    listEl.innerHTML = '';
    show('empty-state');
    return;
  }

  hide('empty-state');
  listEl.innerHTML = state.registrations.map(r => `
    <div class="registration-card">
      <div class="reg-card-main">
        <div class="reg-repo">
          <span class="reg-repo-icon">📦</span>
          <strong>${escapeHtml(r.owner)}/${escapeHtml(r.repo)}</strong>
        </div>
        <div class="reg-details">
          <span class="reg-tag ${r.workflow_installed ? 'tag-green' : 'tag-amber'}">
            ${r.workflow_installed ? 'Workflow' : 'Workflow pending'}
          </span>
          <span class="reg-tag ${r.azure_workspace_name ? 'tag-green' : 'tag-amber'}">
            ${r.azure_workspace_name ? '✓ ' + escapeHtml(r.azure_workspace_name) : '⚠ Workspace skipped'}
          </span>
          <span class="reg-tag tag-muted">
            ${formatDate(r.created_at)}
          </span>
        </div>
      </div>
      <div class="reg-card-actions">
        <button class="btn btn-sm btn-secondary" onclick="viewRegistration('${r.id}')">View</button>
        <button class="btn btn-sm btn-danger-outline" onclick="deleteRegistration('${r.id}')">Remove</button>
      </div>
    </div>
  `).join('');
}

function startNewRegistration() {
  state.currentStep = 1;
  state.currentRegistrationId = null;
  state.github = { connected: false, token: null, installedRepo: null };
  state.azure = { connected: false, skipped: false, token: null, subscriptionId: null, workspaceId: null, workspaceName: null, customerId: null, envVars: null };

  showWizard();
  const patInput = document.getElementById('inputPAT');
  if (patInput) patInput.value = '';
  const ownerInput = document.getElementById('inputOwner');
  if (ownerInput) ownerInput.value = '';
  const repoInput = document.getElementById('inputRepo');
  if (repoInput) repoInput.value = '';
}

function exitSetupWizard() {
  if (!state.currentRegistrationId && !state.github.connected) {
    state.currentStep = 1;
    state.github = { connected: false, token: null, installedRepo: null };
    state.azure = { connected: false, skipped: false, token: null, subscriptionId: null, workspaceId: null, workspaceName: null, customerId: null, envVars: null };
  }

  const result = document.getElementById('workflow-install-result');
  if (result) {
    result.className = 'hidden';
    result.textContent = '';
  }

  showDashboard();
}

function handleDashboardNav(event) {
  if (!state.user) {
    return true;
  }

  if (event) {
    event.preventDefault();
  }

  if (state.currentView === 'wizard') {
    exitSetupWizard();
  } else {
    showDashboard();
  }

  return false;
}

function viewRegistration(id) {
  const reg = state.registrations.find(r => r.id === id);
  if (!reg) return;

  state.currentRegistrationId = id;
  state.currentStep = 3;
  state.github = {
    connected: reg.workflow_installed,
    token: null,
    installedRepo: `${reg.owner}/${reg.repo}`,
  };
  state.azure = {
    connected: !!reg.azure_workspace_name,
    skipped: !reg.azure_workspace_name,
    token: null,
    subscriptionId: reg.azure_subscription_id,
    workspaceId: reg.azure_workspace_id,
    workspaceName: reg.azure_workspace_name,
    customerId: reg.azure_customer_id,
    envVars: null,
  };
  showWizard();
}

async function deleteRegistration(id) {
  if (!confirm('Remove this registration? The workflow file will remain in the repository.')) return;
  try {
    const resp = await fetch(`/api/registrations/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
    });
    if (!resp.ok) throw new Error('Delete failed');
    await loadRegistrations();
  } catch (err) {
    alert(`Failed to remove: ${err.message}`);
  }
}

// ── Step navigation ────────────────────────────────────────────────────────
function goToStep(n) {
  if (n > 1 && !state.github.connected) {
    state.currentStep = 1;
    renderStep(1);
    const result = document.getElementById('workflow-install-result');
    if (result) {
      result.className = 'error-tag';
      result.textContent = 'Connect a repository in Step 1 before continuing to the next step.';
      show('workflow-install-result');
    }
    const repoInput = document.getElementById('inputRepo');
    if (repoInput) repoInput.focus();
    return;
  }
  state.currentStep = n;
  renderStep(n);
}

function renderStep(n) {
  document.querySelectorAll('.wizard-step').forEach(el => el.classList.add('hidden'));
  const el = document.getElementById(`step-${n}`);
  if (el) el.classList.remove('hidden');

  const azureWarning = !!(state.azure.skipped && !state.azure.connected);

  for (let i = 1; i <= 3; i++) {
    const pill = document.getElementById(`pill-${i}`);
    if (!pill) continue;
    pill.classList.remove('active', 'done', 'warning');
    if (i < n) {
      if (i === 2 && azureWarning) pill.classList.add('warning');
      else pill.classList.add('done');
    }
    if (i === n) pill.classList.add('active');
    if (i === 2 && n === 2 && azureWarning) pill.classList.add('warning');
  }

  setStepIcon(1, state.github.connected ? '✓' : '1');
  setStepIcon(2, state.azure.connected ? '✓' : (azureWarning ? '!' : '2'));
  setStepIcon(3, '3');

  const pct = { 1: 16, 2: 50, 3: 100 };
  const progressBar = document.getElementById('progressBar');
  if (progressBar) {
    progressBar.style.width = (pct[n] || 0) + '%';
    progressBar.classList.toggle('warning', azureWarning && n >= 2);
  }

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
    status.textContent = '✓';
    show('github-connected-panel');
    hide('github-connect-actions');
    const detail = document.getElementById('github-connected-detail');
    detail.textContent = `Workflow installed in ${state.github.installedRepo || 'your repository'}.`;
  } else {
    status.textContent = '';
    hide('github-connected-panel');
    show('github-connect-actions');
  }
}

async function installWorkflow() {
  const pat   = document.getElementById('inputPAT').value.trim();
  const owner = document.getElementById('inputOwner').value.trim();
  const repo  = document.getElementById('inputRepo').value.trim();
  const result = document.getElementById('workflow-install-result');

  if (!pat) { alert('Please enter your GitHub Personal Access Token.'); return; }
  if (!owner || !repo) {
    result.className = 'error-tag';
    result.textContent = !repo
      ? 'Repository name is required. Registration cannot be created without a repository.'
      : 'Repository owner is required. Registration cannot be created without owner/repository.';
    show('workflow-install-result');
    if (!repo) {
      const repoInput = document.getElementById('inputRepo');
      if (repoInput) repoInput.focus();
    }
    return;
  }

  result.className = 'loading-tag';
  result.textContent = 'Validating token & installing workflow…';
  show('workflow-install-result');

  try {
    const resp = await fetch('/api/setup/github/install-workflow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ owner, repo, token: pat }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));

    result.className = 'success-banner';
    result.innerHTML = `Workflow installed in <strong>${escapeHtml(owner)}/${escapeHtml(repo)}</strong>.
      ${data.commit_url ? `<br><a href="${escapeHtml(data.commit_url)}" target="_blank">View commit ↗</a>` : ''}`;

    state.github.connected = true;
    state.github.token = pat;
    state.github.installedRepo = `${owner}/${repo}`;
    if (data.registration_id) {
      state.currentRegistrationId = data.registration_id;
    }
    renderStep1();
  } catch (err) {
    result.className = 'error-tag';
    result.textContent = `Failed: ${err.message}`;
  }
}

// ── Step 2: Azure ──────────────────────────────────────────────────────────
function renderStep2() {
  const status = document.getElementById('step2-status');
  if (state.azure.connected) {
    status.textContent = '✓';
    show('azure-connected-panel');
    hide('azure-auth-actions');
    hide('azure-picker-panel');
    document.getElementById('azure-connected-detail').textContent =
      `Connected to ${state.azure.workspaceName || 'your workspace'} successfully.`;
  } else if (state.azure.skipped) {
    status.textContent = '⚠';
    show('azure-auth-actions');
    hide('azure-picker-panel');
    hide('azure-connected-panel');
  } else if (state.azure.token) {
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

function skipAzureSetup() {
  state.azure.connected = false;
  state.azure.skipped = true;
  state.azure.token = null;
  state.azure.subscriptionId = null;
  state.azure.workspaceId = null;
  state.azure.workspaceName = null;
  state.azure.customerId = null;
  state.azure.envVars = null;
  goToStep(3);
}

async function startAzureConnect() {
  const btn = document.getElementById('btnAzureSignIn');
  btn.disabled = true;
  btn.textContent = 'Redirecting…';
  try {
    state.azure.skipped = false;
    sessionStorage.setItem('prism_registrationId', state.currentRegistrationId || '');
    sessionStorage.setItem('prism_githubRepo', state.github.installedRepo || '');
    sessionStorage.setItem('prism_githubConnected', state.github.connected ? '1' : '');

    const resp = await fetch('/api/setup/azure/auth-url');
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    window.location.href = data.url;
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Sign in with Microsoft';
    alert(`Could not start Azure connection: ${err.message}`);
  }
}

async function loadSubscriptions() {
  const list = document.getElementById('sub-tile-list');
  list.innerHTML = '<div class="tile-loading"><div class="tile-spinner"></div>Loading subscriptions…</div>';
  try {
    const resp = await fetch(`/api/setup/azure/subscriptions`, {
      headers: { 'Authorization': `Bearer ${state.azure.token}` },
    });
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    const subs = data.subscriptions || [];
    if (subs.length === 0) {
      list.innerHTML = '<div class="tile-empty-hint">No subscriptions found</div>';
      return;
    }
    list.innerHTML = subs.map(sub => `
      <button class="tile-item" data-id="${escapeHtml(sub.id)}" data-name="${escapeHtml(sub.display_name)}" onclick="selectSubscription(this)">
        <div class="tile-item-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>
        </div>
        <div class="tile-item-body">
          <span class="tile-item-name">${escapeHtml(sub.display_name)}</span>
          <span class="tile-item-meta">${escapeHtml(sub.id)}</span>
        </div>
        <div class="tile-item-check">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
      </button>
    `).join('');
  } catch (err) {
    list.innerHTML = `<div class="tile-empty-hint tile-error">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

function selectSubscription(el) {
  // Deselect siblings
  el.parentElement.querySelectorAll('.tile-item').forEach(t => t.classList.remove('selected'));
  el.classList.add('selected');

  const subId = el.dataset.id;
  state.azure.subscriptionId = subId;

  // Activate workspace section
  const wsSection = document.getElementById('ws-picker-section');
  wsSection.classList.remove('dimmed');
  loadWorkspaces(subId);
}

async function loadWorkspaces(subId) {
  const list = document.getElementById('ws-tile-list');
  const btnConnectWs = document.getElementById('btnConnectWs');
  btnConnectWs.style.display = 'none';
  list.innerHTML = '<div class="tile-loading"><div class="tile-spinner"></div>Loading workspaces…</div>';

  try {
    const resp = await fetch(
      `/api/setup/azure/workspaces/${encodeURIComponent(subId)}`,
      { headers: { 'Authorization': `Bearer ${state.azure.token}` } }
    );
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    const workspaces = data.workspaces || [];
    if (workspaces.length === 0) {
      list.innerHTML = '<div class="tile-empty-hint">No workspaces found in this subscription</div>';
      return;
    }
    list.innerHTML = workspaces.map(ws => `
      <button class="tile-item" data-value='${escapeHtml(JSON.stringify(ws))}' onclick="selectWorkspace(this)">
        <div class="tile-item-icon tile-item-icon-ws">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
        </div>
        <div class="tile-item-body">
          <span class="tile-item-name">${escapeHtml(ws.name)}</span>
          <span class="tile-item-meta">${escapeHtml(ws.resource_group)}</span>
        </div>
        <div class="tile-item-check">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
      </button>
    `).join('');
  } catch (err) {
    list.innerHTML = `<div class="tile-empty-hint tile-error">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

function selectWorkspace(el) {
  el.parentElement.querySelectorAll('.tile-item').forEach(t => t.classList.remove('selected'));
  el.classList.add('selected');
  state._selectedWsRaw = el.dataset.value;
  document.getElementById('btnConnectWs').style.display = '';
}

function filterTiles(listId, query) {
  const list = document.getElementById(listId);
  if (!list) return;
  const q = query.toLowerCase();
  list.querySelectorAll('.tile-item').forEach(tile => {
    const name = (tile.dataset.name || tile.querySelector('.tile-item-name')?.textContent || '').toLowerCase();
    const meta = (tile.querySelector('.tile-item-meta')?.textContent || '').toLowerCase();
    tile.style.display = (name.includes(q) || meta.includes(q)) ? '' : 'none';
  });
}

async function connectWorkspace() {
  const result = document.getElementById('workspace-connect-result');
  const raw = state._selectedWsRaw;

  if (!raw) { alert('Please select a Log Analytics workspace.'); return; }

  let ws;
  try { ws = JSON.parse(raw); } catch (_) { alert('Invalid workspace data.'); return; }

  result.className = 'loading-tag';
  result.textContent = 'Connecting workspace…';
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
    state.azure.skipped      = false;
    state.azure.workspaceId  = ws.id;
    state.azure.workspaceName = ws.name;
    state.azure.customerId   = data.config?.customer_id;
    state.azure.envVars      = data.env_vars;

    if (state.currentRegistrationId) {
      try {
        const patchResp = await fetch(`/api/registrations/${encodeURIComponent(state.currentRegistrationId)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            azure_subscription_id: state.azure.subscriptionId,
            azure_workspace_id: ws.id,
            azure_workspace_name: ws.name,
            azure_customer_id: data.config?.customer_id || '',
          }),
        });
        if (!patchResp.ok) {
          console.warn('Failed to update registration with Azure info:', patchResp.status);
        }
      } catch (patchErr) {
        console.warn('Failed to update registration with Azure info:', patchErr);
      }
    } else {
      console.warn('No currentRegistrationId — Azure workspace info was NOT saved to the database.');
    }

    renderStep2();
    goToStep(3);
  } catch (err) {
    result.className = 'error-tag';
    result.textContent = `Failed: ${err.message}`;
  }
}

// ── Step 3: Complete ───────────────────────────────────────────────────────
function renderStep3() {
  const azureSkipped = !state.azure.connected && !!state.azure.skipped;
  const finishCard = document.querySelector('#step-3 .step-card-finish');
  const finishTitle = document.getElementById('step3-title');
  const finishSubtitle = document.getElementById('step3-subtitle');
  const finishCircle = document.getElementById('finish-check-circle');
  const finishMark = document.getElementById('finish-check-mark');

  if (finishCard) finishCard.classList.toggle('warning', azureSkipped);
  if (finishTitle) {
    finishTitle.textContent = azureSkipped ? 'Setup Complete with Limited History' : 'Setup Complete';
  }
  if (finishSubtitle) {
    finishSubtitle.textContent = azureSkipped
      ? 'Azure setup was skipped. History Agent will have no incident data until you connect a workspace.'
      : 'Your PRism registration is configured and ready to go.';
  }
  if (finishCircle) {
    finishCircle.setAttribute('stroke', azureSkipped ? '#d29922' : '#238636');
    finishCircle.setAttribute('fill', azureSkipped ? 'rgba(187,128,9,0.14)' : 'rgba(46,160,67,0.1)');
  }
  if (finishMark) {
    finishMark.setAttribute('stroke', azureSkipped ? '#f2cc60' : '#3fb950');
  }

  const ghDetail = document.getElementById('summary-github-detail');
  const ghCard = document.getElementById('finish-github-card');
  if (state.github.connected) {
    ghDetail.textContent = `Workflow installed in ${state.github.installedRepo || 'your repository'}`;
    if (ghCard) ghCard.style.borderColor = 'rgba(46,160,67,.4)';
  } else {
    ghDetail.textContent = 'Not yet connected';
  }

  const azDetail = document.getElementById('summary-azure-detail');
  const azCard = document.getElementById('finish-azure-card');
  if (state.azure.connected) {
    azDetail.textContent = `✓ Connected to ${state.azure.workspaceName || 'your workspace'}`;
    if (azCard) azCard.style.borderColor = 'rgba(46,160,67,.4)';
  } else {
    azDetail.textContent = '⚠ Skipped — History agent will have no incident data';
    if (azCard) azCard.style.borderColor = 'rgba(187,128,9,.4)';
  }
}

async function checkOrchestratorHealth() {
  const tag = document.getElementById('orchestrator-status');
  const link = document.getElementById('orchestrator-health-link');

  try {
    const resp = await fetch('/health');
    if (!resp.ok) throw new Error(`/health returned ${resp.status}`);
    const data = await resp.json();
    const orchUrl = data.orchestrator_url || '';
    if (link) {
      link.href = `${orchUrl}/health`;
      link.textContent = `${orchUrl}/health ↗`;
    }

    try {
      const orchResp = await fetch(`${orchUrl}/health`, { signal: AbortSignal.timeout(5000) });
      if (orchResp.ok) {
        tag.className = 'ok-tag';
        tag.textContent = `Orchestrator is reachable at ${orchUrl}`;
      } else {
        tag.className = 'error-tag';
        tag.textContent = `Orchestrator returned HTTP ${orchResp.status}`;
      }
    } catch (_) {
      tag.className = 'error-tag';
      tag.textContent = `Could not reach orchestrator at ${orchUrl}`;
    }
  } catch (_) {
    tag.className = 'error-tag';
    tag.textContent = 'Could not load platform health info';
  }
}

function finishSetup() {
  showDashboard();
}
