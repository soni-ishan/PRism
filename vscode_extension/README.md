<p align="center">
  <img src="media/prism-icon.png" alt="PRism" width="100" />
</p>

# PRism — Deployment Risk Intelligence

AI-powered pre-deployment risk gate for VS Code. Shows a **Deployment Confidence Score**, per-agent risk findings, and rollback playbooks right in your sidebar — before you merge.

## Features

- **Deployment Confidence Score** — A 0–100 risk score computed across four AI agents analyzing your PR diff, test coverage, incident history, and deploy timing
- **Per-Agent Risk Cards** — See exactly which agent flagged what: diff dangers, test regressions, historical incidents, and deployment window risk
- **Microsoft Foundry Integration** — Leverages PRism's own Azure OpenAI model (GPT-4o-mini) deployed via Microsoft Foundry, with Azure Content Safety guardrails on every output
- **Smart Fetching** — First reads the existing PRism review comment from your open PR (free, no API call consumed). Falls back to calling `/analyze` directly with your local git diff only if no PR comment exists
- **Auto-refresh on Commit** — Sidebar automatically refreshes after every `git commit` using VS Code's built-in git extension events
- **One-click Re-run** — Refresh button in the sidebar toolbar triggers an immediate fresh analysis
- **Freemium Credit Meter** — Shows remaining free-tier runs (500 total). Displays a warning when credits are exhausted

## Getting Started

1. Install the **PRism** extension from the VS Code Marketplace (publisher: `thegooddatalab`)
2. Open a repository in VS Code — on first activation, approve the GitHub authorization prompt (recommended for the best results)
3. The PRism sidebar appears in the Activity Bar — click it to see your current deployment confidence score
4. Optionally install the PRism Gate workflow in your repo via the [PRism Setup Wizard](https://github.com/soni-ishan/PRism) to get automated PR comments alongside the sidebar

No additional setup required. The extension connects to the hosted PRism backend automatically.

**Special Offer for AI Dev Days Hackathon Judges:** The extension ships with **500 free analysis runs** using PRism's own Azure OpenAI model on Microsoft Foundry. No Azure subscription needed.

## Extension Settings

| Setting | Default | Description |
|---|---|---|
| `prism.serverUrl` | Hosted PRism instance on Azure Container Apps | URL of the PRism FastAPI orchestrator backend. Override to point at a self-hosted deployment. |
| `prism.githubToken` | *(empty — uses VS Code built-in auth)* | Optional GitHub PAT with `repo` scope. Used to fetch the existing PR analysis comment so the sidebar matches the PR comment exactly. |

## Commands

- **PRism: Re-run Analysis** (`prism.rerunAnalysis`) — Trigger an immediate fresh analysis. Also available as the refresh icon in the sidebar toolbar.
- **PRism: Show Full Report** (`prism.showReport`) — Open the full risk report in a separate panel.

## How the Sidebar Works

On first activation the extension prompts you to **authorize your GitHub account** via VS Code's built-in GitHub authentication. Your choice determines how analysis runs:

### Without GitHub authorization

The extension performs a **local analysis** using VS Code's git extension to extract the diff and changed files:
- Diff Analyst and Coverage Agent analyze your local `git diff HEAD~1`
- Timing Agent always runs (only needs the current timestamp)
- History Agent is skipped — it requires GitHub repository context to look up past production incidents
- One analysis run is consumed from the 500-run free tier

### With GitHub authorization (recommended)

The extension connects to your GitHub account, finds the open PR for your current branch, and reads the **existing PRism deployment analysis comment** directly from the PR:
- Zero free-tier runs consumed — just reading a comment
- Most accurate results — full repo context including History Agent incident data
- Consistent view — you see exactly the same assessment as your team members on GitHub

### Trigger events

The extension **never polls on a timer**. Analysis only runs when:
- The extension opens on a new repo/folder (once per workspace)
- You make a `git commit` (VS Code git commit events trigger a refresh)
- You click the Re-run button in the sidebar toolbar

### Credit tracking

A persistent `clientId` (UUID stored in VS Code `globalState`) is sent as `X-Client-ID` on every backend call for freemium tracking. The `/usage` endpoint shows remaining credits. When credits are exhausted (HTTP 402), a notification appears with an "Open Settings" link to configure a self-hosted backend.

## Requirements

- VS Code `^1.85.0`
- A GitHub repository with the [PRism Gate workflow](https://github.com/soni-ishan/PRism) configured (auto-installed via the Setup Wizard)

## Release Notes

### 0.1.6

- Freemium credit meter with 500 free runs for hackathon judges
- Smart fetch: reads existing PR comment before calling `/analyze`
- Auto-refresh triggered by VS Code git commit events
- HTTP 402 handling with "Open Settings" notification

### 0.1.0

Initial release of PRism — Deployment Risk Intelligence.
