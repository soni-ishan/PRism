"""
PRism Shared Data Contract
==========================
Every specialist agent MUST return an AgentResult conforming to this schema.
The Verdict Agent aggregates multiple AgentResult payloads into a VerdictReport.

RepoContext carries per-registration configuration (PAT, Azure workspace)
so the orchestrator and agents can operate across multiple repos/workspaces.

This module is the single source of truth for inter-agent communication.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class RepoContext(BaseModel):
    """Per-registration context that flows through the orchestration pipeline.

    Built by the orchestrator server from a platform ``RegistrationRow``.
    Agents that need repo-specific credentials (Coverage, History) receive
    this instead of relying on global environment variables.
    """

    registration_id: Optional[str] = Field(
        default=None,
        description="Platform registration UUID (for audit / logging)",
    )
    owner: str = Field(
        ..., description="GitHub repo owner (org or user)"
    )
    repo: str = Field(
        ..., description="GitHub repo name (without owner prefix)"
    )
    gh_token: Optional[str] = Field(
        default=None,
        description="Decrypted GitHub PAT for this registration",
    )
    azure_search_endpoint: Optional[str] = Field(
        default=None,
        description="Azure AI Search endpoint for this workspace",
    )
    azure_search_key: Optional[str] = Field(
        default=None,
        description="Azure AI Search key for this workspace",
    )
    azure_tenant_id: Optional[str] = Field(
        default=None,
        description="Customer's Azure AD tenant ID (for cross-tenant Log Analytics access)",
    )
    azure_workspace_id: Optional[str] = Field(
        default=None,
        description="Azure Log Analytics workspace ID",
    )
    azure_customer_id: Optional[str] = Field(
        default=None,
        description="Azure Log Analytics customer ID",
    )
    azure_search_index: Optional[str] = Field(
        default=None,
        description="Per-repo AI Search index name (e.g. incidents-owner-repo)",
    )

    @property
    def full_repo(self) -> str:
        """Return 'owner/repo' string."""
        return f"{self.owner}/{self.repo}"

    @property
    def effective_index_name(self) -> str | None:
        """Return the AI Search index name for this repo, or ``None``.

        Returns the explicit ``azure_search_index`` when set.  Returns
        ``None`` when no workspace has been linked — callers should treat
        this as "no deployment connection".
        """
        return self.azure_search_index or None


def derive_index_name(owner: str, repo: str) -> str:
    """Derive a deterministic AI Search index name from owner/repo.

    Azure AI Search index names must be lowercase, start with a letter,
    and contain only letters, digits, or dashes.
    """
    raw = f"incidents-{owner}-{repo}".lower()
    sanitised = "".join(c if c.isalnum() or c == "-" else "-" for c in raw)
    # Collapse consecutive dashes and strip trailing dashes
    while "--" in sanitised:
        sanitised = sanitised.replace("--", "-")
    return sanitised.strip("-")


class AgentResult(BaseModel):
    """Standardised payload returned by every specialist agent.

    Attributes:
        agent_name:          Human-readable identifier for the agent.
        risk_score_modifier: Integer 0-100 indicating how much risk this
                             agent's analysis adds (0 = safe, 100 = critical).
        status:              Categorical severity — "pass", "warning", or "critical".
        findings:            List of specific, actionable findings.
        recommended_action:  Plain-English recommendation for the Verdict Agent.
    """

    agent_name: str = Field(
        ...,
        description="Human-readable identifier for the agent",
    )
    risk_score_modifier: int = Field(
        ...,
        ge=0,
        le=100,
        description="Risk modifier from 0 (perfectly safe) to 100 (critical failure)",
    )
    status: Literal["pass", "warning", "critical"] = Field(
        ...,
        description="Categorical severity level",
    )
    findings: list[str] = Field(
        default_factory=list,
        description="Specific, actionable findings from the agent's analysis",
    )
    recommended_action: str = Field(
        ...,
        description="Plain-English recommendation for the Verdict Agent",
    )

    # ── Serialisation helpers ──────────────────────────────────────────

    def to_json(self) -> str:
        """Serialize this result to a JSON string."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "AgentResult":
        """Parse and validate a JSON string into an AgentResult.

        Raises:
            pydantic.ValidationError: If the JSON does not match the schema.
            json.JSONDecodeError:     If *raw* is not valid JSON.
        """
        data = json.loads(raw)
        return cls.model_validate(data)


class VerdictReport(BaseModel):
    """Final aggregated output produced by the Verdict Agent.

    Attributes:
        confidence_score:  Overall deployment confidence (0-100).
        decision:          "greenlight" (>= 70) or "blocked" (< 70 or any critical).
        risk_brief:        Human-readable markdown summary of all findings.
        rollback_playbook: Markdown rollback plan (present only when blocked).
        agent_results:     The raw payloads from all specialist agents.
    """

    confidence_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Overall deployment confidence score (0-100)",
    )
    decision: Literal["greenlight", "blocked"] = Field(
        ...,
        description="Deployment decision based on the confidence score and critical overrides",
    )
    risk_brief: str = Field(
        ...,
        description="Human-readable markdown summary of all findings",
    )
    rollback_playbook: str | None = Field(
        default=None,
        description="Markdown rollback plan; present only when decision is 'blocked'",
    )
    agent_results: list[AgentResult] = Field(
        default_factory=list,
        description="Raw payloads from all specialist agents",
    )

    # ── Invariant enforcement ──────────────────────────────────────────

    @model_validator(mode="after")
    def _check_invariants(self) -> "VerdictReport":
        has_critical = any(r.status == "critical" for r in self.agent_results)
        if has_critical and self.decision != "blocked":
            raise ValueError("Decision must be 'blocked' when any agent is critical")
        if self.confidence_score < 70 and self.decision != "blocked":
            raise ValueError("Decision must be 'blocked' when confidence_score < 70")
        if self.decision == "greenlight" and self.rollback_playbook is not None:
            raise ValueError("rollback_playbook must be None when decision is 'greenlight'")
        return self

    # ── Serialisation helpers ──────────────────────────────────────────

    def to_json(self) -> str:
        """Serialize this report to a JSON string."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "VerdictReport":
        """Parse and validate a JSON string into a VerdictReport.

        Raises:
            pydantic.ValidationError: If the JSON does not match the schema.
            json.JSONDecodeError:     If *raw* is not valid JSON.
        """
        data = json.loads(raw)
        return cls.model_validate(data)
