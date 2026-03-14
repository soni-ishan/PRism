/**
 * PRism — auth.js
 *
 * Authentication (GitHub OAuth), view routing, and shared helpers.
 * Loaded on every page that needs login awareness.
 */

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  user: null,
  registrations: [],
  currentView: 'loading',
  currentStep: 1,
  currentRegistrationId: null,
  github: {
    connected: false,
    token: null,
    installedRepo: null,
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
document.addEventListener('DOMContentLoaded', async () => {
  readAzureCallbackParams();
  await checkAuth();
});

async function checkAuth() {
  try {
    const resp = await fetch('/api/auth/me', { credentials: 'same-origin' });
    if (resp.ok) {
      state.user = await resp.json();
      renderHeader();
      if (state._showWizardAfterAuth) {
        state._showWizardAfterAuth = false;
        showWizard();
        renderStep(2);
      } else if (typeof showDashboard === 'function') {
        await showDashboard();
      }
    } else {
      state.user = null;
      renderHeader();
      if (typeof showLogin === 'function') showLogin();
    }
  } catch (_) {
    state.user = null;
    renderHeader();
    if (typeof showLogin === 'function') showLogin();
  }
}

// ── Header rendering ───────────────────────────────────────────────────────
function renderHeader() {
  const container = document.getElementById('header-right');
  if (!container) return;

  if (state.user) {
    container.innerHTML = `
      <div class="user-badge">
        <img src="${escapeHtml(state.user.avatar_url)}" alt="" class="user-avatar" />
        <span class="user-name">${escapeHtml(state.user.username)}</span>
        <button class="btn btn-sm btn-secondary" onclick="logout()">Sign out</button>
      </div>
    `;
  } else {
    container.innerHTML = '';
  }
}

// ── View switching ─────────────────────────────────────────────────────────
function showLogin() {
  state.currentView = 'login';
  // On the landing page, just show the login section
  show('login-page');
  hide('dashboard-page');
  hide('wizard-container');
}

async function showDashboard() {
  state.currentView = 'dashboard';
  hide('login-page');
  show('dashboard-page');
  hide('wizard-container');
  window.history.replaceState({}, document.title, '/app.html#dashboard');
  if (typeof loadRegistrations === 'function') await loadRegistrations();
}

function showWizard() {
  state.currentView = 'wizard';
  hide('login-page');
  hide('dashboard-page');
  show('wizard-container');
  if (typeof renderStep === 'function') renderStep(state.currentStep);
  if (typeof checkOrchestratorHealth === 'function') checkOrchestratorHealth();
}

// ── Authentication ─────────────────────────────────────────────────────────
async function logout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
  } catch (_) { /* ignore */ }
  state.user = null;
  state.registrations = [];
  renderHeader();
  // Redirect to landing page on logout
  window.location.href = '/';
}

// ── Azure OAuth callback ───────────────────────────────────────────────────
function readAzureCallbackParams() {
  let fragment = window.location.hash.substring(1);
  let params = new URLSearchParams(fragment);
  if (!params.has('azure_connected')) {
    params = new URLSearchParams(window.location.search);
    fragment = window.location.search;
  }

  if (params.get('azure_connected') === 'true') {
    state.azure.connected = false;
    const token = params.get('azure_token');
    if (token) state.azure.token = token;
    state.currentStep = 2;

    const savedRegId = sessionStorage.getItem('prism_registrationId');
    if (savedRegId) state.currentRegistrationId = savedRegId;
    const savedRepo = sessionStorage.getItem('prism_githubRepo');
    if (savedRepo) {
      state.github.connected = true;
      state.github.installedRepo = savedRepo;
    }
    sessionStorage.removeItem('prism_registrationId');
    sessionStorage.removeItem('prism_githubRepo');
    sessionStorage.removeItem('prism_githubConnected');

    state._showWizardAfterAuth = true;
  }

  if (fragment) {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
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

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch (_) { return iso; }
}
