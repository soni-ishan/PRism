# PRism — Deployment Risk Intelligence

AI-powered pre-deployment risk gate for VS Code. Shows a **Deployment Confidence Score**, per-agent risk findings, and rollback playbooks right in your sidebar - before you merge.

## Features

- **Deployment Confidence Score** - A 0–100 risk score computed across multiple AI agents analysing your PR diff
- **Per-Agent Risk Cards** - See exactly which agent flagged what: test coverage, dependency drift, rollback readiness, and more
- **Auto-refresh** - Sidebar polls the PRism backend on a configurable interval so your score stays current as you push commits
- **One-click Re-run** - Manually trigger a fresh analysis at any time from the sidebar toolbar

## Getting Started

1. Install the extension from the VS Code Marketplace
2. Open a repository that has the PRism Gate workflow (`.github/workflows/prism-gate.yml`)
3. The PRism sidebar will appear in the Activity Bar - click it to see your current deployment risk score

No additional setup is required. The extension connects to the hosted PRism backend automatically.

## Extension Settings

| Setting | Default | Description |
|---|---|---|
| `prism.serverUrl` | Hosted PRism instance | URL of the PRism FastAPI backend. Override to point at a self-hosted deployment. |
| `prism.autoRefresh` | `true` | Automatically poll and refresh the sidebar at a fixed interval. |
| `prism.refreshIntervalSeconds` | `30` | Polling interval in seconds for auto-refresh. |

## Commands

- **PRism: Re-run Analysis** - Trigger an immediate fresh analysis
- **PRism: Show Full Report** - Open the full risk report

## Requirements

- VS Code `^1.85.0`
- A GitHub repository with the [PRism Gate workflow](https://github.com/soni-ishan/PRism) configured

## Release Notes

### 0.1.0

Initial release of PRism - Deployment Risk Intelligence.
