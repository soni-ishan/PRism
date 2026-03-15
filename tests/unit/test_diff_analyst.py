import json
import os
from unittest.mock import patch

import pytest

from agents.diff_analyst.diff_agent import (
    _fallback,
    _safe_findings_list,
    heuristic_scan,
    run,
)
from agents.shared.data_contract import AgentResult

# ---------------------------------------------------------------------------
# heuristic_scan – deterministic, no I/O
# ---------------------------------------------------------------------------

def test_detects_secret():
    diff = '+ API_KEY="sk-1234567890abcdef"'
    risk, status, findings = heuristic_scan(diff)

    assert status == "critical"
    assert risk >= 85
    assert len(findings) > 0


def test_detects_aws_key():
    # AKIA followed by exactly 16 uppercase alphanumeric characters = valid AWS key format
    diff = "+ key = AKIAIOSFODNN7EXAMPLE"
    risk, status, findings = heuristic_scan(diff)

    assert status == "critical"
    assert risk >= 85


def test_detects_github_token():
    diff = "+ token = ghp_abcdefghijklmnopqrstu"
    risk, status, findings = heuristic_scan(diff)

    assert status == "critical"
    assert risk >= 85


def test_detects_removed_retry():
    diff = "- retry_count = 3\n+ pass"
    risk, status, findings = heuristic_scan(diff)

    assert status in ["warning", "critical"]
    assert any("retry" in f.lower() for f in findings)


def test_detects_removed_error_handling():
    diff = (
        "- try:\n"
        "-     do_payment()\n"
        "- except Exception:\n"
        "-     handle_error()\n"
        "+ do_payment()"
    )
    risk, status, findings = heuristic_scan(diff)

    assert status == "warning"
    assert any("error handling" in f.lower() for f in findings)


def test_schema_change_detection():
    diff = "+ ALTER TABLE users DROP COLUMN password"
    risk, status, findings = heuristic_scan(diff)

    assert status in ["warning", "critical"]
    assert len(findings) > 0


def test_clean_diff_returns_pass():
    diff = "+ print('hello world')"
    risk, status, findings = heuristic_scan(diff)

    assert status == "pass"
    assert risk == 0
    assert findings == []


# ---------------------------------------------------------------------------
# _safe_findings_list helper
# ---------------------------------------------------------------------------

def test_safe_findings_list_none():
    assert _safe_findings_list(None) == []


def test_safe_findings_list_list():
    result = _safe_findings_list(["a", "b", 3])
    assert result == ["a", "b", "3"]


def test_safe_findings_list_scalar():
    assert _safe_findings_list("single finding") == ["single finding"]


# ---------------------------------------------------------------------------
# _fallback helper
# ---------------------------------------------------------------------------

def test_fallback_default():
    result = _fallback("something went wrong")
    assert isinstance(result, AgentResult)
    assert result.agent_name == "Diff Analyst"
    assert result.status == "warning"
    assert result.risk_score_modifier == 60
    assert "something went wrong" in result.findings


def test_fallback_with_existing_findings():
    result = _fallback("reason", h_risk=80, h_status="critical", h_findings=["finding1"])
    assert result.status == "critical"
    assert result.risk_score_modifier == 80
    assert "finding1" in result.findings


def test_fallback_clamps_risk_score():
    result = _fallback("reason", h_risk=200)
    assert result.risk_score_modifier == 100

    result_low = _fallback("reason", h_risk=-10)
    assert result_low.risk_score_modifier == 0


# ---------------------------------------------------------------------------
# run() – public async entrypoint, all branches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_empty_diff_returns_warning():
    result = await run("", [])

    assert isinstance(result, AgentResult)
    assert result.agent_name == "Diff Analyst"
    assert result.status == "warning"
    assert result.risk_score_modifier == 30
    assert any("No diff content" in f for f in result.findings)


@pytest.mark.asyncio
async def test_run_whitespace_diff_returns_warning():
    result = await run("   \n\t  ", [])

    assert result.status == "warning"
    assert result.risk_score_modifier == 30


@pytest.mark.asyncio
async def test_run_secret_in_diff_returns_critical(monkeypatch):
    # Ensure Azure env vars are absent so we don't attempt real LLM calls.
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    diff = '+ password="supersecret123"'
    result = await run(diff, ["auth.py"])

    assert result.status == "critical"
    assert result.risk_score_modifier >= 85
    assert len(result.findings) > 0


@pytest.mark.asyncio
async def test_run_no_azure_env_pass_path(monkeypatch):
    """When Azure env vars are absent and heuristics find no issues, status is pass."""
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    diff = "+ result = 1 + 1"
    result = await run(diff, ["math.py"])

    assert result.status == "pass"
    assert result.risk_score_modifier == 0
    assert any("No critical anti-patterns" in f for f in result.findings)


@pytest.mark.asyncio
async def test_run_no_azure_env_warning_path(monkeypatch):
    """When Azure env vars are absent and heuristics find issues, status reflects heuristics."""
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    diff = "- retry_count = 3\n+ pass"
    result = await run(diff, ["service.py"])

    assert result.status == "warning"
    assert result.risk_score_modifier > 0


@pytest.mark.asyncio
async def test_run_llm_valid_json_response(monkeypatch):
    """With Azure env vars set and call_llm mocked, uses LLM-provided JSON output."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fake-deployment")

    llm_payload = json.dumps({
        "risk_score_modifier": 45,
        "status": "warning",
        "findings": ["Potential missing null check in payment flow."],
        "recommended_action": "Add null guard before payment call.",
    })

    with patch("agents.diff_analyst.diff_agent.call_llm", return_value=llm_payload):
        result = await run("+ process_payment(user)", ["payment.py"])

    assert isinstance(result, AgentResult)
    assert result.agent_name == "Diff Analyst"
    assert result.status == "warning"
    assert result.risk_score_modifier >= 45
    assert any("null check" in f for f in result.findings)


@pytest.mark.asyncio
async def test_run_llm_pass_injects_default_findings(monkeypatch):
    """LLM returns pass + empty findings → agent injects standard pass findings."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fake-deployment")

    llm_payload = json.dumps({
        "risk_score_modifier": 5,
        "status": "pass",
        "findings": [],
        "recommended_action": "Looks good.",
    })

    with patch("agents.diff_analyst.diff_agent.call_llm", return_value=llm_payload):
        result = await run("+ x = 1", [])

    assert result.status == "pass"
    assert result.risk_score_modifier <= 20
    assert any("No critical anti-patterns" in f for f in result.findings)


@pytest.mark.asyncio
async def test_run_llm_pass_overridden_by_heuristics(monkeypatch):
    """LLM says pass but heuristics detected an issue → status is upgraded to warning."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fake-deployment")

    llm_payload = json.dumps({
        "risk_score_modifier": 10,
        "status": "pass",
        "findings": ["No issues found."],
        "recommended_action": "No action needed.",
    })

    diff = "- retry_count = 3\n+ pass"

    with patch("agents.diff_analyst.diff_agent.call_llm", return_value=llm_payload):
        result = await run(diff, [])

    # Heuristics found retry removal; status must be at least warning
    assert result.status in ("warning", "critical")


@pytest.mark.asyncio
async def test_run_llm_invalid_json_falls_back(monkeypatch):
    """When call_llm returns non-JSON content, run() falls back to heuristics."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fake-deployment")

    with patch("agents.diff_analyst.diff_agent.call_llm", return_value="This is not JSON at all."):
        result = await run("+ safe_change()", ["utils.py"])

    assert isinstance(result, AgentResult)
    assert result.agent_name == "Diff Analyst"
    assert result.status in ("warning", "critical")


@pytest.mark.asyncio
async def test_run_changed_files_included_in_context(monkeypatch):
    """Changed files list is forwarded through without raising errors."""
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    changed_files = [f"service_{i}.py" for i in range(250)]
    result = await run("+ x = 1", changed_files)

    assert isinstance(result, AgentResult)
    assert result.agent_name == "Diff Analyst"


@pytest.mark.asyncio
async def test_run_unknown_status_in_llm_response_normalized(monkeypatch):
    """Unknown status string in LLM JSON is normalised to 'warning'."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fake-deployment")

    llm_payload = json.dumps({
        "risk_score_modifier": 40,
        "status": "unknown_value",
        "findings": ["Some finding."],
        "recommended_action": "Check manually.",
    })

    with patch("agents.diff_analyst.diff_agent.call_llm", return_value=llm_payload):
        result = await run("+ change()", [])

    assert result.status == "warning"