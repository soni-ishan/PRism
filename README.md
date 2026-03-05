# PRism 🔬
### Deployment Risk Intelligence, Powered by Agentic AI

> *Every engineer has shipped a breaking change. Tests passed. Linter was clean. And then production went down.*
>
> *PRism exists because "tests pass" ≠ "safe to ship."*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Azure](https://img.shields.io/badge/Deployed%20on-Azure-0078D4?logo=microsoft-azure)](https://azure.microsoft.com)
[![Built with Microsoft Foundry](https://img.shields.io/badge/Built%20with-Microsoft%20Foundry-5C2D91)](https://ai.azure.com)
[![GitHub Copilot](https://img.shields.io/badge/Enhanced%20by-GitHub%20Copilot-000000?logo=github)](https://github.com/features/copilot)

---

## The Problem

Current CI/CD pipelines are **binary and stateless**. They ask one question: *"Did the tests pass?"*

They never ask:
- Has this file caused production incidents before?
- Did test coverage actually drop for these new code paths?
- Are we deploying at 4:58 PM on a Friday before a long weekend?
- Was retry logic silently removed from a payment-critical path?

This makes deployment decisions feel like guesswork — and sometimes, they are. The result is incidents that were entirely preventable, post-mortems written at 2 AM, and engineers afraid to merge.

**PRism changes that.** Instead of a binary pass/fail gate, PRism gives every PR a **Deployment Confidence Score (0–100)** — a multi-agent risk assessment that considers code quality, historical incidents, test coverage, and operational timing, all in real time.

---

## How It Works

PRism triggers automatically when a PR is opened or updated. Four specialized AI agents analyze the change **in parallel**, each returning a structured JSON payload via a shared Data Contract. The Verdict Agent ingests all four payloads and converges on a single governed decision.

```
GitHub PR Opened / Updated
          │
          ▼
┌─────────────────────────────────────────────┐
│            ORCHESTRATOR AGENT               │
│       (Microsoft Agent Framework)           │
│    Governed by Microsoft Foundry            │
└──┬──────────┬──────────┬──────────┬─────────┘
   │          │          │          │
   ▼          ▼          ▼          ▼
[Diff     [History   [Coverage  [Timing        ← Parallel execution
Analyst]  Agent]     Agent]     Agent]
   │          │          │          │
   └──────────┴──────────┴──────────┘
                    │
             📋 Data Contract
          (unified JSON schema)
                    │
                    ▼
            VERDICT AGENT
      Deployment Confidence Score
           + Risk Brief
           + Rollback Playbook
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
    Score ≥ 70            Score < 70
    ✅ Greenlight          🚫 Block Deploy
                          + Auto-generate
                            missing tests
                            via Copilot
                          + Rollback plan
```

---

## The Agents

| Agent | Signal | Hero Tech |
|---|---|---|
| **Diff Analyst** | Scans PR diff for dangerous patterns — removed retry logic, missing error handlers, schema changes, hardcoded secrets | GitHub MCP Server |
| **History Agent** | Correlates changed files with past incidents using semantic search — *"payment_service.py in 4 of last 8 incidents"* | Azure MCP + Azure AI Search |
| **Coverage Agent** | Detects test coverage regression; triggers Copilot Coding Agent to auto-write missing tests and open a new PR | GitHub Copilot Coding Agent |
| **Timing Agent** | Flags high-risk deployment windows — Friday deploys, peak traffic periods, pre-release proximity | Microsoft Agent Framework |
| **Verdict Agent** | Ingests all four JSON payloads, computes Deployment Confidence Score, generates risk brief and rollback playbook | Microsoft Foundry (governed) |

---

## The Data Contract

Every specialist agent returns a **standardized JSON payload**. This enables true parallel execution — the Orchestrator dispatches all four agents simultaneously and the Verdict Agent aggregates without sequential handoffs.

```json
{
  "agent_name": "string",
  "risk_score_modifier": 25,
  "status": "warning",
  "findings": [
    "Specific finding 1",
    "Specific finding 2"
  ],
  "recommended_action": "Plain-English recommendation for the Verdict Agent."
}
```

| Field | Type | Description |
|---|---|---|
| `agent_name` | string | Identifier for the agent |
| `risk_score_modifier` | integer 0–100 | 0 = perfectly safe, 100 = critical failure |
| `status` | enum | `"pass"` · `"warning"` · `"critical"` |
| `findings` | string[] | Specific, actionable findings |
| `recommended_action` | string | Plain-English recommendation for aggregation |

---

## Sample Output

A PR comment posted automatically by PRism:

```
🔬 PRism Deployment Risk Assessment

Confidence Score: 21 / 100  ⛔ HIGH RISK — Deploy Blocked

Risk Brief:
  • payment_service.py linked to 4 of the last 8 production incidents
  • Test coverage dropped 9% (3 new functions have no tests)
  • Deployment window: Friday 4:47 PM — historically high incident rate
  • retry logic removed from a payment-critical path

Action Taken:
  ✅ Missing tests auto-generated by Copilot → PR #47 opened for review
  📋 Rollback playbook generated → docs/rollback/pr-46-playbook.md

To override, a maintainer with write access must manually approve.
```

---

## Tech Stack

| Category | Technology |
|---|---|
| Agent Orchestration | Microsoft Agent Framework (Semantic Kernel + AutoGen) |
| Platform & Governance | Microsoft Foundry |
| Tool Connectivity | Azure MCP Server, GitHub MCP Server |
| Semantic Incident Search | Azure AI Search (built-in vectorization) |
| Code Generation | GitHub Copilot Coding Agent |
| Cloud Infrastructure | Azure (Monitor, Functions, Container Apps) |
| IDE Integration | VS Code Extension (TypeScript) |
| Backend | Python (agents) |
| Version Control | GitHub |

---

## Project Structure

```
prism/
├── .github/
│   ├── workflows/
│   │   └── ci.yml                  # PRism dogfoods its own CI
│   └── CODEOWNERS                  # Folder-level ownership enforced
├── agents/
│   ├── orchestrator/               # Ishan — parallel dispatch + wiring
│   ├── diff_analyst/               # Ina — dangerous pattern scanner
│   ├── history_agent/              # Simar — Azure AI Search correlation
│   ├── coverage_agent/             # Ama — regression + Copilot fix
│   ├── timing_agent/               # Ishan — deploy window risk
│   └── verdict_agent/              # Ishan — score aggregation + Foundry
├── mcp_servers/
│   └── github_connector/           # GitHub MCP server configuration
├── vscode_extension/               # PRism Confidence Sidebar (TypeScript)
├── foundry/
│   └── deployment_config/          # Azure Foundry governance config
├── docs/
│   ├── architecture_diagram.md     # Mermaid system diagram
│   └── demo_script.md              # 2-minute demo storyboard
└── tests/
```

---

## Hackathon

**AI Dev Days Hackathon — Microsoft, 2026**

**Challenge:** *Automate and Optimize Software Delivery — Leverage Agentic DevOps Principles*

**Target prizes:** Grand Prize (Agentic DevOps) · Best Multi-Agent System · Best Enterprise Solution · Best Azure Integration

PRism directly addresses the challenge criteria: intelligent CI/CD pipelines with agent orchestration, automated incident response, and real-time reliability monitoring — with a pre-deployment gate that no other project in the field has.

---

## Setup

> ⚠️ **Prerequisites:** Azure subscription, GitHub account, VS Code with GitHub Copilot extension

```bash
# Clone the repo
git clone https://github.com/soni-ishan/PRism.git
cd PRism

# Install Python dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Fill in Azure credentials:
#   - AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
#   - AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY
#   - AZURE_FOUNDRY_PROJECT_CONNECTION_STRING
#   - GITHUB_TOKEN

# Setup Azure AI Search with sample incident data (required for History Agent)
python tests/test_azure_search.py

# Run tests
python test_integration.py

# Run the orchestrator locally
uvicorn agents.orchestrator.server:app --reload --port 8000
```

Full Azure deployment: see [`foundry/deployment_config/README.md`](foundry/deployment_config/README.md)

History Agent + Azure Function IaC deployment:
- `deploy/history_agent/README.md`
- `deploy/ingestion_function/README.md`

---

## Branching Convention

```
feature/<your-initial>-<agent-name>
# e.g. feature/simar-history-agent
#      feature/ina-diff-analyst
#      feature/ama-coverage-agent
```

Work only inside your designated `agents/` subfolder. Do not modify `agents/orchestrator/` or `agents/verdict_agent/` without raising an issue first.

---

## Team

Built by **The Good Data Lab** for the Microsoft AI Dev Days Hackathon 2026.

| Member | Role | Owns |
|---|---|---|
| **Ishan Soni** | Architect | Orchestrator · Verdict Agent · Timing Agent · VS Code Extension · Foundry |
| **Simarpreet Purba** | Toolsmith | History Agent · Azure AI Search · Azure MCP |
| **Gurinayat Mangat** | Analyst | Diff Analyst Agent · GitHub MCP · Pattern Detection |
| **Favour (Ama) Ejike** | QA & Coverage | Coverage Agent · Copilot Integration · Tests · Demo Video |

---

## License

MIT — see [LICENSE](LICENSE)