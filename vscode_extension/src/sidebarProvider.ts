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


  public static readonly viewType = "prism.sidebar";

  private _view?: vscode.WebviewView;
  private _latestVerdict: VerdictReport = MOCK_VERDICT;
  private _cachedBranch: string | undefined;
  private _cachedRepo: string | null = null;
  private clientId: string;

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

    const verdict = await this._fetchVerdict();
    if (verdict) {
      this._latestVerdict = verdict;
    }
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
    const config = vscode.workspace.getConfiguration("prism");
    const baseUrl = config.get<string>("serverUrl", "http://localhost:8000");

    // Use cached git info (populated by refresh())
    const branch = this._cachedBranch;
    const prNumber = this._extractPrNumber(branch);
    const repo = this._cachedRepo;

    const payload = {
      pr_number: prNumber ?? 0,
      repo: repo ?? "unknown/repo",
      changed_files: [],
      diff: "",
      timestamp: new Date().toISOString(),
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
  </style>
</head>
<body>
  <div class="branch-info">
    Branch: <strong>${this._escapeHtml(this._cachedBranch ?? "unknown")}</strong>
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

  <script>
    const vscode = acquireVsCodeApi();
    function post(command) { vscode.postMessage({ command }); }
  </script>
</body>
</html>`;
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
