<p align="center">
  <img src="vscode_extension/media/prism-icon.png" alt="PRism" width="120" />
</p>

# PRism - Deployment Risk Intelligence

### Agentic Pre-Deployment Risk Gate, Powered by Microsoft AI Platform

> *Every engineer has shipped a breaking change. Tests passed. Linter was clean. And then production went down.*
>
> *PRism exists because "tests pass" ≠ "safe to ship."*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Azure](https://img.shields.io/badge/Deployed%20on-Azure-0078D4?logo=microsoft-azure)](https://azure.microsoft.com)
[![Built with Microsoft Foundry](https://img.shields.io/badge/Built%20with-Microsoft%20Foundry-5C2D91)](https://ai.azure.com)
[![GitHub Copilot](https://img.shields.io/badge/Enhanced%20by-GitHub%20Copilot-000000?logo=github)](https://github.com/features/copilot)

---

**🎬 [Watch the 2-minute Demo on YouTube](https://www.youtube.com/watch?v=3jAxC7I3zYk)**

| | |
|---|---|
| 🏗️ Architecture | [architecture.mermaid](https://github.com/soni-ishan/PRism/blob/main/architecture.mermaid) |
| 🌐 Setup Platform | [prism-dev-platform.orangemushroom-cc646ad1.eastus2.azurecontainerapps.io](https://prism-dev-platform.orangemushroom-cc646ad1.eastus2.azurecontainerapps.io/) |
| 🔌 VS Code Extension | [marketplace.visualstudio.com](https://marketplace.visualstudio.com/items?itemName=thegooddatalab.prism-risk-gate) |
| 🎬 Demo Video | [youtube.com/watch?v=3jAxC7I3zYk](https://www.youtube.com/watch?v=3jAxC7I3zYk) |

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

## How It Works (High-Level Overview)

PRism triggers automatically when a PR is opened or updated. Four specialized AI agents analyze the change **in parallel**, each returning a structured JSON payload via a shared Data Contract. The Verdict Agent ingests all four payloads and converges on a single governed decision.

```
Developer opens PR on GitHub
          │
          ▼
  prism-gate.yml (GitHub Actions)
  auto-installed by Setup Wizard
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
    ✅ Greenlight          ⛔ Block Deploy
                          + Auto-generate
                            missing tests
                            via Copilot
                          + Rollback plan
```

**See [architecture.mermaid](architecture.mermaid) for the detailed system diagram ([view on GitHub](https://github.com/soni-ishan/PRism/blob/main/architecture.mermaid)).**

---

## The Agents

| Agent | Signal | Hero Tech |
|---|---|---|
| **Diff Analyst** | Scans PR diff for dangerous patterns — removed retry logic, missing error handlers, schema changes, hardcoded secrets | GitHub MCP Server + Azure OpenAI |
| **History Agent** | Correlates changed files with past production incidents via semantic search — *"payment_service.py involved in 4 of last 8 incidents"* | Azure MCP Server + Azure AI Search |
| **Coverage Agent** | Detects test coverage regression; triggers GitHub Copilot Coding Agent to auto-write missing tests and open a new PR | GitHub Copilot Coding Agent |
| **Timing Agent** | Flags high-risk deployment windows — Friday deploys, after-hours merges, pre-release proximity, US federal holidays | Microsoft Agent Framework |
| **Verdict Agent** | Ingests all four JSON payloads, computes Deployment Confidence Score (0–100), generates risk brief and rollback playbook via GPT-4o-mini with Azure Content Safety guardrails | Microsoft Foundry |

**Agent Weights:** Diff Analyst 30% · History Agent 25% · Coverage Agent 25% · Timing Agent 20%

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
  📋 Rollback playbook generated → attached to this PR comment

To override, a maintainer with write access must manually approve.
```

---

## Tech Stack

| Category | Technology |
|---|---|
| **Agent Orchestration** | Microsoft Agent Framework (Semantic Kernel) |
| **AI Models** | Azure OpenAI GPT-4o-mini (Sweden Central) via Microsoft Foundry |
| **Platform & Governance** | Microsoft Azure AI Foundry (`azure-ai-projects`) |
| **Observability** | OpenTelemetry + Azure Monitor / Application Insights |
| **Content Safety** | Azure Content Safety |
| **Tool Connectivity** | Azure MCP Server · GitHub MCP Server |
| **Semantic Incident Search** | Azure AI Search (BM25 + semantic ranking) |
| **Incident Ingestion** | Azure Functions (Timer + Event Grid + HTTP triggers) |
| **Code Generation** | GitHub Copilot Coding Agent (auto-generates missing tests) |
| **Cloud Infrastructure** | Azure Container Apps · Azure PostgreSQL · Azure Container Registry |
| **Infrastructure as Code** | Bicep + PowerShell deploy scripts |
| **Backend** | Python 3.12 / FastAPI / Uvicorn |
| **Database** | SQLite (dev) · PostgreSQL via asyncpg (production) |
| **Authentication** | GitHub OAuth2 · JWT (PyJWT) · Fernet AES encryption |
| **IDE Integration** | VS Code Extension (TypeScript) |
| **CI/CD** | GitHub Actions (self-dogfooding with `prism-gate.yml`) |

---

## Project Structure

```
PRism/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                  # PRism dogfeeds its own CI gate
│   │   ├── prism-gate.yml          # Workflow auto-installed in customer repos
│   │   ├── deploy-azure.yml        # Deploy orchestrator to Azure Container Apps
│   │   └── deploy-platform.yml     # Deploy platform to Azure Container Apps
│   └── CODEOWNERS
├── agents/
│   ├── orchestrator/               # Parallel dispatch, FastAPI webhook server (:8000)
│   ├── diff_analyst/               # Dangerous pattern scanner (heuristics + LLM)
│   ├── history_agent/              # Azure AI Search incident correlator
│   ├── coverage_agent/             # Test regression detector + Copilot trigger
│   ├── timing_agent/               # Deploy window risk (pure deterministic)
│   ├── verdict_agent/              # Score aggregator + Foundry governance
│   └── shared/                     # AgentResult + VerdictReport data contracts
├── platform/
│   ├── server/                     # FastAPI onboarding backend (:8080)
│   │   ├── routers/                # auth, github_setup, azure_setup, registrations
│   │   └── services/               # auth_service, github_service, azure_service, db
│   └── frontend/                   # Setup wizard (vanilla HTML/CSS/JS)
├── mcp_servers/
│   ├── azure_mcp_server/           # Azure AI Search wrapper + incident ingestion
│   └── github_connector/           # GitHub MCP server configuration
├── foundry/
│   └── deployment_config/          # Bicep IaC templates + deploy/cleanup scripts
├── function_deploy/                # Azure Function app (incident ingestion triggers)
├── vscode_extension/               # PRism Confidence Sidebar (TypeScript)
├── tests/                          # Unit + integration test suite
├── architecture.mermaid            # System architecture diagram
├── requirements.txt
└── .env.example
```

---

## Hackathon

**AI Dev Days Hackathon — Microsoft, February 10 – March 15, 2026**

**Challenge:** *Automate and Optimize Software Delivery — Agentic DevOps*

**Target prizes:** Grand Prize (Agentic DevOps) · Best Multi-Agent System · Best Enterprise Solution · Best Azure Integration

**🎬 [Watch our 2-minute Demo Video on YouTube](https://www.youtube.com/watch?v=3jAxC7I3zYk)**

PRism directly addresses the challenge criteria: intelligent CI/CD pipelines with agent orchestration, automated incident response, and real-time reliability monitoring — with a pre-deployment risk gate that tests against real-world production state, not just isolated code.

**For Judges:** Download our [VS Code extension](https://marketplace.visualstudio.com/items?itemName=thegooddatalab.prism-risk-gate) and experience PRism from your own workspace. We cover up to **500 analysis runs** using PRism's own Azure OpenAI model deployed on Microsoft Foundry — no Azure subscription required on your end. You can also try the live [Setup Platform](https://prism-dev-platform.orangemushroom-cc646ad1.eastus2.azurecontainerapps.io/) to onboard a repo in under 3 minutes.

---

## Team

Built by **The Good Data Lab** for the Microsoft AI Dev Days Hackathon 2026.

| Member | Owns |
|---|---|
| **Ishan Soni** | Orchestrator · Verdict Agent · Timing Agent · Foundry Governance · VS Code Extension · GitHub Actions & CI/CD · PR Comment Posting |
| **Simarpreet Purba** | History Agent · Azure MCP Server · Landing/Setup Platform · Incident Ingestion Pipeline · OAuth Flows · Bicep IaC |
| **Gurinayat Mangat** | Diff Analyst Agent · LLM Analysis · GitHub MCP Client · Heuristic Pattern Detection · PR Comment CI Risk Brief · Demo Video |
| **Favour Ejike** | Coverage Agent · Copilot Integration · Test Coverage Detection · Demo Video |

---

## License

MIT — see [LICENSE](LICENSE)
