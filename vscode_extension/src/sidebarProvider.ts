/**
 * PRism Sidebar Provider
 * ======================
 * Implements `WebviewViewProvider` to render the Deployment Confidence
 * sidebar panel.  Fetches data from the PRism FastAPI backend (`/analyze`)
 * and renders a score gauge, risk findings, and action links.
 *
 * Falls back to mock data when the backend is unreachable so the UX
 * can be demonstrated without a running server.
 */

import * as vscode from "vscode";
import * as cp from "child_process";

// ── Types mirroring the Python data contract ────────────────────────

interface AgentResult {
  agent_name: string;
  risk_score_modifier: number;
  status: "pass" | "warning" | "critical";
  findings: string[];
  recommended_action: string;
}

interface VerdictReport {
  confidence_score: number;
  decision: "greenlight" | "blocked";
  risk_brief: string;
  rollback_playbook: string | null;
  agent_results: AgentResult[];
}

interface UsageInfo {
  unlimited: boolean;
  credits_used: number;
  credits_limit: number | null;
  credits_remaining: number | null;
}

// ── Mock data for demo / offline mode ───────────────────────────────

const MOCK_VERDICT: VerdictReport = {
  confidence_score: 21,
  decision: "blocked",
  risk_brief: "",
  rollback_playbook:
    "## Rollback Playbook\n\n```bash\ngit revert HEAD\ngit push origin main\n```\n\n### Flagged Agents\n- Diff Analyst (critical)\n- History Agent (warning)\n\n### Re-submission Checklist\n- [ ] Address retry logic removal\n- [ ] Add missing test coverage\n- [ ] Re-run PRism analysis",
  agent_results: [
    {
      agent_name: "Diff Analyst",
      risk_score_modifier: 85,
      status: "critical",
      findings: [
        "Retry logic removed from payment_service.py",
        "Error handling downgraded: except clause replaced with bare pass",
      ],
      recommended_action: "Restore retry logic or add equivalent fault tolerance",
    },
    {
      agent_name: "History Agent",
      risk_score_modifier: 40,
      status: "warning",
      findings: [
        "payment_service.py linked to 4 of last 8 production incidents",
        "Most recent: Payment processing failures (2026-02-25, critical)",
      ],
      recommended_action: "Extra review recommended for high-incident file",
    },
    {
      agent_name: "Coverage Agent",
      risk_score_modifier: 55,
      status: "critical",
      findings: [
        "Test coverage dropped 9% (78% → 69%)",
        "3 new functions have zero test coverage",
      ],
      recommended_action: "Add tests for uncovered functions before merging",
    },
    {
      agent_name: "Timing Agent",
      risk_score_modifier: 55,
      status: "critical",
      findings: [
        "Friday 4:47 PM — historically high incident window",
        "Deploy window outside core hours (after 4 PM)",
      ],
      recommended_action: "Delay deployment to Monday morning",
    },
  ],
};

// ── Provider ────────────────────────────────────────────────────────

export class SidebarProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "prism.sidebar";

  private _view?: vscode.WebviewView;
  private _latestVerdict: VerdictReport = MOCK_VERDICT;
  private _cachedBranch: string | undefined;
  private _cachedRepo: string | null = null;
  private clientId: string;
  private _dataSource: string = '';
  private _usageInfo: UsageInfo | null = null;

  constructor(private readonly _extensionUri: vscode.Uri, clientId: string) {
    this.clientId = clientId;
  }

  // Called by VS Code when the sidebar is first revealed
  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this._extensionUri, "media"),
        vscode.Uri.joinPath(this._extensionUri, "out"),
      ],
    };

    // Handle messages from the webview
    webviewView.webview.onDidReceiveMessage((msg) => {
      switch (msg.command) {
        case "refresh":
          this.refresh();
          break;
        case "showReport":
          this.showFullReport();
          break;
        case "showRollback":
          this._showRollbackPlaybook();
          break;
      }
    });

    this._render();
    this.refresh(); // attempt live fetch on first open
  }

  /** Re-fetch from the backend and re-render. */
  public async refresh(): Promise<void> {
    // Refresh cached git info asynchronously (non-blocking)
    [this._cachedBranch, this._cachedRepo] = await Promise.all([
      this._getCurrentBranch(),
      this._detectRepo(),
    ]);

    const [verdict, usage] = await Promise.all([
      this._fetchVerdict(),
      this._fetchUsage(),
    ]);
    if (verdict) {
      this._latestVerdict = verdict;
    }
    this._usageInfo = usage;
    this._render();
  }

  /** Open the full risk brief in an editor panel (like SnipSage's webview panel). */
  public showFullReport(): void {
    const panel = vscode.window.createWebviewPanel(
      "prism.report",
      "PRism — Full Risk Report",
      vscode.ViewColumn.Beside,
      {}
    );
    panel.webview.html = this._buildReportHtml(this._latestVerdict);
  }

  // ── Private Helpers ───────────────────────────────────────────────

  /** Detect the current Git branch name asynchronously. */
  private _getCurrentBranch(): Promise<string | undefined> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
      return Promise.resolve(undefined);
    }
    return new Promise((resolve) => {
      cp.execFile(
        "git",
        ["rev-parse", "--abbrev-ref", "HEAD"],
        { cwd: workspaceFolder.uri.fsPath, encoding: "utf-8" },
        (err, stdout) => {
          resolve(err ? undefined : stdout.trim());
        }
      );
    });
  }

  /** Fetch a VerdictReport from the PRism backend `/analyze` endpoint. */
  private async _fetchVerdict(): Promise<VerdictReport | null> {
    const branch = this._cachedBranch;
    const repo = this._cachedRepo;

    // ── Path 1: fetch the existing PRism review comment from GitHub ──────
    // This is free (no /analyze call), always matches the PR comment exactly,
    // and requires the branch to have an open PR and a GitHub token.
    if (repo && branch) {
      const token = await this._getGitHubToken();
      if (token) {
        const pr = await this._findOpenPR(repo, branch, token);
        if (pr) {
          const verdict = await this._fetchPRReview(repo, pr.number, token);
          if (verdict) {
            this._dataSource = `PR #${pr.number}`;
            return verdict;
          }
        }
      }
    }

    // ── Path 2: call /analyze with local git history (fallback) ──────────
    // Used when there is no open PR, no GitHub token, or no PRism review yet.
    const config = vscode.workspace.getConfiguration("prism");
    const baseUrl = config.get<string>("serverUrl", "http://localhost:8000");
    const prNumber = this._extractPrNumber(branch);

    const [changedFiles, diff, commitTimestamp] = await Promise.all([
      this._getChangedFiles(),
      this._getDiff(),
      this._getCommitTimestamp(),
    ]);

    this._dataSource = 'local commit';

    const payload = {
      pr_number: prNumber ?? 0,
      repo: repo ?? "unknown/repo",
      changed_files: changedFiles,
      diff: diff,
      timestamp: commitTimestamp ?? this._localIso(),
    };

    try {
      const response = await fetch(`${baseUrl}/analyze`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Client-ID": this.clientId,
        },
        body: JSON.stringify(payload),
      });

      if (response.status === 402) {
        vscode.window.showWarningMessage(
          "PRism Trial Exhausted! Please configure your Enterprise Server URL to continue.",
          "Open Settings"
        ).then(selection => {
          if (selection === "Open Settings") {
            vscode.commands.executeCommand('workbench.action.openSettings', 'prism.serverUrl');
          }
        });
        return null;
      }

      if (!response.ok) {
        console.warn(`PRism backend returned ${response.status}`);
        return null;
      }

      return (await response.json()) as VerdictReport;
    } catch (err) {
      // Backend unreachable — use mock data silently
      console.debug("PRism backend unreachable, using mock data:", err);
      return null;
    }
  }

  /** Fetch the current freemium credit usage from the backend `/usage` endpoint. */
  private async _fetchUsage(): Promise<UsageInfo | null> {
    const config = vscode.workspace.getConfiguration("prism");
    const baseUrl = config.get<string>("serverUrl", "http://localhost:8000");
    try {
      const resp = await fetch(`${baseUrl}/usage`, {
        headers: { "X-Client-ID": this.clientId },
      });
      if (!resp.ok) { return null; }
      return (await resp.json()) as UsageInfo;
    } catch {
      return null;
    }
  }

  // ── GitHub PR review helpers ──────────────────────────────────────

  /**
   * Get a GitHub access token.
   * Prefers VS Code's built-in GitHub auth (silent, no prompt).
   * Falls back to the `prism.githubToken` PAT setting.
   */
  private async _getGitHubToken(): Promise<string | null> {
    try {
      const session = await vscode.authentication.getSession(
        'github', ['repo'], { createIfNone: false }
      );
      if (session?.accessToken) { return session.accessToken; }
    } catch { /* auth provider not available */ }

    const pat = vscode.workspace.getConfiguration("prism").get<string>("githubToken");
    return pat || null;
  }

  /** Find the open PR for the given branch using the GitHub API. */
  private async _findOpenPR(repo: string, branch: string, token: string): Promise<{ number: number } | null> {
    const owner = repo.split('/')[0];
    try {
      const resp = await fetch(
        `https://api.github.com/repos/${repo}/pulls?state=open&head=${encodeURIComponent(owner + ':' + branch)}&per_page=1`,
        { headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' } }
      );
      if (!resp.ok) { return null; }
      const prs = (await resp.json()) as any[];
      return prs[0] ? { number: prs[0].number } : null;
    } catch { return null; }
  }

  /**
   * Fetch the most recent PRism review comment for the PR and parse it.
   * PRism comments are posted via the GitHub Pulls Reviews API so they appear
   * as reviews of type COMMENT with a body starting with "## PRism Deployment Risk Analysis".
   */
  private async _fetchPRReview(repo: string, prNumber: number, token: string): Promise<VerdictReport | null> {
    try {
      const resp = await fetch(
        `https://api.github.com/repos/${repo}/pulls/${prNumber}/reviews?per_page=100`,
        { headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' } }
      );
      if (!resp.ok) { return null; }
      const reviews = (await resp.json()) as any[];

      // Find the most recent PRism review
      const prismReview = reviews
        .filter(r => typeof r.body === 'string' && r.body.includes('## PRism Deployment Risk Analysis'))
        .sort((a, b) => new Date(b.submitted_at).getTime() - new Date(a.submitted_at).getTime())[0];

      return prismReview ? this._parsePRComment(prismReview.body) : null;
    } catch { return null; }
  }

  /**
   * Parse a PRism PR review comment body into a VerdictReport.
   *
   * Comment format (from prism-gate.yml):
   *   **Confidence Score:** `87 / 100`
   *   **Verdict:** ✅ GREENLIGHT  or  🚫 BLOCKED
   *   | Agent | Status | Risk | Key Finding |
   *   | Diff Analyst | ✅ pass | 5 | ... |
   *   <details>...<summary>📋 Full Risk Brief</summary>brief text</details>
   */
  private _parsePRComment(body: string): VerdictReport | null {
    const scoreMatch = body.match(/`(\d+)\s*\/\s*100`/);
    if (!scoreMatch) { return null; }
    const confidence_score = parseInt(scoreMatch[1], 10);

    const decision: 'greenlight' | 'blocked' =
      body.includes('GREENLIGHT') ? 'greenlight' : 'blocked';

    // Each data row: | agent | emoji status | risk_modifier | finding |
    const validStatuses = new Set(['pass', 'warning', 'critical']);
    const agentResults: AgentResult[] = [];
    const rowRegex = /\|\s*([^|]+?)\s*\|\s*\S+\s+(\w+)\s*\|\s*(\d+)\s*\|\s*([^|]*?)\s*\|/g;
    let m: RegExpExecArray | null;
    while ((m = rowRegex.exec(body)) !== null) {
      const status = m[2].trim().toLowerCase();
      if (!validStatuses.has(status)) { continue; }
      agentResults.push({
        agent_name: m[1].trim(),
        status: status as 'pass' | 'warning' | 'critical',
        risk_score_modifier: parseInt(m[3], 10),
        findings: m[4].trim() ? [m[4].trim()] : [],
        recommended_action: '',
      });
    }

    const briefMatch = body.match(/<\/summary>([\s\S]*?)<\/details>/);
    const risk_brief = briefMatch ? briefMatch[1].trim() : '';

    return { confidence_score, decision, risk_brief, rollback_playbook: null, agent_results: agentResults };
  }

  /** ISO-8601 timestamp in the local timezone (fallback when no commit exists). */
  private _localIso(): string {
    const now = new Date();
    const offsetMins = -now.getTimezoneOffset();
    const sign = offsetMins >= 0 ? "+" : "-";
    const pad2 = (n: number) => String(Math.abs(n)).padStart(2, "0");
    return (
      `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}` +
      `T${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}` +
      `${sign}${pad2(Math.floor(Math.abs(offsetMins) / 60))}:${pad2(Math.abs(offsetMins) % 60)}`
    );
  }

  /** Files changed in the latest commit (`git diff-tree --no-commit-id -r --name-only HEAD`). */
  private _getChangedFiles(): Promise<string[]> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) { return Promise.resolve([]); }
    return new Promise((resolve) => {
      cp.execFile(
        "git",
        ["diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
        { cwd: workspaceFolder.uri.fsPath, encoding: "utf-8" },
        (err, stdout) => {
          resolve(err ? [] : stdout.trim().split("\n").filter(Boolean));
        }
      );
    });
  }

  /**
   * Unified diff of the latest commit (`git diff HEAD~1 HEAD`).
   * Truncated to 20 KB to keep the request payload manageable.
   */
  private _getDiff(): Promise<string> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) { return Promise.resolve(""); }
    return new Promise((resolve) => {
      cp.execFile(
        "git",
        ["diff", "HEAD~1", "HEAD"],
        { cwd: workspaceFolder.uri.fsPath, encoding: "utf-8" },
        (err, stdout) => {
          if (err) { resolve(""); return; }
          const MAX = 20_000;
          resolve(stdout.length > MAX ? stdout.slice(0, MAX) + "\n[diff truncated]" : stdout);
        }
      );
    });
  }

  /** Author timestamp of the latest commit in ISO-8601 format (`git log -1 --format=%aI`). */
  private _getCommitTimestamp(): Promise<string | null> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) { return Promise.resolve(null); }
    return new Promise((resolve) => {
      cp.execFile(
        "git",
        ["log", "-1", "--format=%aI"],
        { cwd: workspaceFolder.uri.fsPath, encoding: "utf-8" },
        (err, stdout) => {
          resolve(err ? null : stdout.trim() || null);
        }
      );
    });
  }

  /** Try to extract a PR number from a branch name like `feature/123-foo` or `pr/42`. */
  private _extractPrNumber(branch: string | undefined): number | null {
    if (!branch) {
      return null;
    }
    const match = branch.match(/(?:pr[/-]|#)(\d+)/i) ?? branch.match(/(\d+)/);
    return match ? parseInt(match[1], 10) : null;
  }

  /** Detect the GitHub repo slug from the git remote asynchronously. */
  private _detectRepo(): Promise<string | null> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
      return Promise.resolve(null);
    }
    return new Promise((resolve) => {
      cp.execFile(
        "git",
        ["remote", "get-url", "origin"],
        { cwd: workspaceFolder.uri.fsPath, encoding: "utf-8" },
        (err, stdout) => {
          if (err) {
            resolve(null);
            return;
          }
          const remote = stdout.trim();
          // https://github.com/owner/repo.git  or  git@github.com:owner/repo.git
          const m = remote.match(/github\.com[:/](.+?)(?:\.git)?$/);
          resolve(m ? m[1] : null);
        }
      );
    });
  }

  /** Show the rollback playbook in a new editor tab. */
  private _showRollbackPlaybook(): void {
    const playbook = this._latestVerdict.rollback_playbook;
    if (!playbook) {
      vscode.window.showInformationMessage("No rollback playbook — deploy is greenlit!");
      return;
    }
    vscode.workspace
      .openTextDocument({ content: playbook, language: "markdown" })
      .then((doc) => vscode.window.showTextDocument(doc));
  }

  /** Render the sidebar webview HTML. */
  private _render(): void {
    if (!this._view) {
      return;
    }
    this._view.webview.html = this._buildSidebarHtml(this._latestVerdict);
  }

  // ── HTML Builders ─────────────────────────────────────────────────

  private _scoreColor(score: number): string {
    if (score >= 70) { return "#4ec9b0"; }  // green
    if (score >= 40) { return "#cca700"; }  // yellow
    return "#f44747";                        // red
  }

  private _statusBadge(status: string): string {
    const colors: Record<string, string> = {
      pass: "#4ec9b0",
      warning: "#cca700",
      critical: "#f44747",
    };
    const color = colors[status] ?? "#888";
    return `<span style="
      display:inline-block;
      padding:2px 8px;
      border-radius:10px;
      font-size:11px;
      font-weight:600;
      color:#fff;
      background:${color};
    ">${status.toUpperCase()}</span>`;
  }

  private _buildSidebarHtml(v: VerdictReport): string {
    const color = this._scoreColor(v.confidence_score);
    const icon = v.decision === "greenlight" ? "✅" : "🚫";
    const decisionText =
      v.decision === "greenlight" ? "Deploy Approved" : "Deploy Blocked";

    // Build agent findings sections
    const agentSections = v.agent_results
      .map(
        (a) => `
      <div class="agent-card">
        <div class="agent-header">
          <span class="agent-name">${this._escapeHtml(a.agent_name)}</span>
          ${this._statusBadge(a.status)}
          <span class="agent-score">+${a.risk_score_modifier}</span>
        </div>
        <ul class="findings">
          ${a.findings.map((f) => `<li>${this._escapeHtml(f)}</li>`).join("")}
        </ul>
        <div class="recommendation">${this._escapeHtml(a.recommended_action)}</div>
      </div>`
      )
      .join("");

    // Gauge SVG (circular arc)
    const pct = v.confidence_score / 100;
    const dashArray = 188.5; // circumference of r=30 semicircle ≈ π*60
    const dashOffset = dashArray * (1 - pct);

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family, system-ui, sans-serif);
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      padding: 12px;
      line-height: 1.5;
    }

    /* ── Score Gauge ─────────────────────────── */
    .gauge-container {
      text-align: center;
      margin-bottom: 16px;
    }
    .gauge-svg {
      width: 140px;
      height: 80px;
    }
    .gauge-bg {
      fill: none;
      stroke: var(--vscode-editorWidget-border, #333);
      stroke-width: 8;
      stroke-linecap: round;
    }
    .gauge-fill {
      fill: none;
      stroke: ${color};
      stroke-width: 8;
      stroke-linecap: round;
      stroke-dasharray: ${dashArray};
      stroke-dashoffset: ${dashOffset};
      transition: stroke-dashoffset 0.6s ease;
    }
    .score-label {
      font-size: 32px;
      font-weight: 700;
      color: ${color};
    }
    .decision-label {
      font-size: 14px;
      font-weight: 600;
      margin-top: 4px;
    }

    /* ── Agent Cards ─────────────────────────── */
    .section-title {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--vscode-descriptionForeground);
      margin: 16px 0 8px;
      border-bottom: 1px solid var(--vscode-editorWidget-border, #333);
      padding-bottom: 4px;
    }
    .agent-card {
      background: var(--vscode-editor-background);
      border: 1px solid var(--vscode-editorWidget-border, #333);
      border-radius: 6px;
      padding: 10px;
      margin-bottom: 8px;
    }
    .agent-header {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }
    .agent-name {
      font-weight: 600;
      font-size: 13px;
      flex: 1;
    }
    .agent-score {
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
      font-family: var(--vscode-editor-font-family, monospace);
    }
    .findings {
      list-style: none;
      padding: 0;
      margin: 0 0 6px;
    }
    .findings li {
      font-size: 12px;
      padding: 2px 0 2px 14px;
      position: relative;
    }
    .findings li::before {
      content: "•";
      position: absolute;
      left: 2px;
      color: var(--vscode-descriptionForeground);
    }
    .recommendation {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      font-style: italic;
    }

    /* ── Action Buttons ──────────────────────── */
    .actions {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 16px;
    }
    .action-btn {
      display: block;
      width: 100%;
      padding: 8px 12px;
      border: none;
      border-radius: 4px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      text-align: center;
      color: var(--vscode-button-foreground);
      background: var(--vscode-button-background);
    }
    .action-btn:hover {
      background: var(--vscode-button-hoverBackground);
    }
    .action-btn.secondary {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
    }
    .action-btn.secondary:hover {
      background: var(--vscode-button-secondaryHoverBackground);
    }

    /* ── Branch info ─────────────────────────── */
    .branch-info {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      text-align: center;
      margin-bottom: 12px;
    }

    /* ── Trial credit bar ────────────────────── */
    .trial-bar {
      margin-top: 16px;
      padding-top: 12px;
      border-top: 1px solid var(--vscode-editorWidget-border, #333);
    }
    .trial-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 5px;
    }
    .trial-count {
      font-weight: 600;
      font-family: var(--vscode-editor-font-family, monospace);
    }
    .trial-track {
      height: 4px;
      background: var(--vscode-editorWidget-border, #444);
      border-radius: 2px;
      overflow: hidden;
    }
    .trial-fill {
      height: 100%;
      border-radius: 2px;
      transition: width 0.4s ease;
    }
    .trial-exhausted {
      font-size: 11px;
      color: #f44747;
      text-align: center;
      padding: 6px 0;
      font-weight: 600;
    }
  </style>
</head>
<body>
  <div class="branch-info">
    Branch: <strong>${this._escapeHtml(this._cachedBranch ?? "unknown")}</strong>
    ${this._dataSource ? `&nbsp;·&nbsp; <span style="opacity:0.7">${this._escapeHtml(this._dataSource)}</span>` : ''}
  </div>

  <!-- Score Gauge -->
  <div class="gauge-container">
    <svg class="gauge-svg" viewBox="0 0 140 80">
      <path class="gauge-bg"
            d="M 10,70 A 60,60 0 0,1 130,70"/>
      <path class="gauge-fill"
            d="M 10,70 A 60,60 0 0,1 130,70"/>
    </svg>
    <div class="score-label">${v.confidence_score}</div>
    <div class="decision-label">${icon} ${decisionText}</div>
  </div>

  <!-- Agent Findings -->
  <div class="section-title">Risk Findings</div>
  ${agentSections}

  <!-- Actions -->
  <div class="actions">
    <button class="action-btn" onclick="post('refresh')">🔄 Re-run Analysis</button>
    <button class="action-btn secondary" onclick="post('showReport')">📋 Full Report</button>
    ${v.rollback_playbook ? '<button class="action-btn secondary" onclick="post(\'showRollback\')">🔙 Rollback Playbook</button>' : ""}
  </div>

  <!-- Trial credit bar -->
  ${this._buildUsageHtml()}

  <script>
    const vscode = acquireVsCodeApi();
    function post(command) { vscode.postMessage({ command }); }
  </script>
</body>
</html>`;
  }

  /** Build the trial credit bar HTML shown at the bottom of the sidebar. */
  private _buildUsageHtml(): string {
    const u = this._usageInfo;
    if (!u) { return ''; }                           // backend unreachable
    if (u.unlimited) { return ''; }                  // self-hosted / enterprise — no bar needed

    const used = u.credits_used;
    const limit = u.credits_limit ?? 0;
    const remaining = u.credits_remaining ?? 0;

    if (limit === 0) { return ''; }

    const pct = Math.min(100, Math.round((used / limit) * 100));
    const barColor = remaining === 0 ? '#f44747'
                   : pct >= 80      ? '#cca700'
                   : '#4ec9b0';

    if (remaining === 0) {
      return `<div class="trial-bar">
        <div class="trial-exhausted">Trial exhausted — configure your server URL in settings</div>
      </div>`;
    }

    return `<div class="trial-bar">
      <div class="trial-header">
        <span>Free Trial</span>
        <span class="trial-count" style="color:${barColor}">${remaining} / ${limit} credits left</span>
      </div>
      <div class="trial-track">
        <div class="trial-fill" style="width:${pct}%; background:${barColor}"></div>
      </div>
    </div>`;
  }

  /** Full report panel HTML — richer layout for an editor tab. */
  private _buildReportHtml(v: VerdictReport): string {
    const color = this._scoreColor(v.confidence_score);
    const icon = v.decision === "greenlight" ? "✅" : "⛔";

    const agentRows = v.agent_results
      .map(
        (a) => `
      <tr>
        <td style="font-weight:600">${this._escapeHtml(a.agent_name)}</td>
        <td>${this._statusBadge(a.status)}</td>
        <td style="text-align:center;font-family:monospace">+${a.risk_score_modifier}</td>
        <td><ul>${a.findings.map((f) => `<li>${this._escapeHtml(f)}</li>`).join("")}</ul></td>
        <td style="font-style:italic;font-size:13px">${this._escapeHtml(a.recommended_action)}</td>
      </tr>`
      )
      .join("");

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <style>
    body {
      font-family: var(--vscode-font-family, system-ui);
      color: var(--vscode-editor-foreground);
      background: var(--vscode-editor-background);
      padding: 24px;
      line-height: 1.6;
    }
    h1 { margin-bottom: 4px; }
    .subtitle { color: var(--vscode-descriptionForeground); margin-bottom: 24px; }
    .score-box {
      display: inline-block;
      font-size: 48px;
      font-weight: 700;
      color: ${color};
      border: 3px solid ${color};
      border-radius: 12px;
      padding: 12px 28px;
      margin-bottom: 24px;
    }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--vscode-editorWidget-border, #333); }
    th { font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--vscode-descriptionForeground); }
    ul { margin: 0; padding-left: 16px; }
    pre { background: var(--vscode-textBlockQuote-background); padding: 12px; border-radius: 6px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>🔬 PRism Deployment Risk Assessment</h1>
  <div class="subtitle">Confidence Score &amp; Agent Breakdown</div>

  <div class="score-box">${v.confidence_score} / 100</div>
  <div style="font-size:18px;font-weight:600;margin-bottom:24px">${icon} ${v.decision === "greenlight" ? "Deploy Approved" : "Deploy Blocked"}</div>

  <h2>Agent Results</h2>
  <table>
    <thead>
      <tr><th>Agent</th><th>Status</th><th>Modifier</th><th>Findings</th><th>Recommendation</th></tr>
    </thead>
    <tbody>
      ${agentRows}
    </tbody>
  </table>

  ${
    v.rollback_playbook
      ? `<h2 style="margin-top:32px">Rollback Playbook</h2><pre>${this._escapeHtml(v.rollback_playbook)}</pre>`
      : ""
  }
</body>
</html>`;
  }

  private _escapeHtml(text: string): string {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}
