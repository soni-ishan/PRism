"""
PRism Shared Data Contract
==========================
Every specialist agent MUST return an AgentResult conforming to this schema.
The Verdict Agent aggregates multiple AgentResult payloads into a VerdictReport.

This module is the single source of truth for inter-agent communication.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator  # type: ignore


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
