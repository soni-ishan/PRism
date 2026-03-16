from agents.diff_analyst.diff_agent import heuristic_scan, run
from textwrap import dedent
import asyncio
import json
import os
from unittest.mock import patch

def test_detects_secret():
    diff = """
+ API_KEY="sk-1234567890abcdef"
"""
    risk, status, findings = heuristic_scan(diff)

    assert status == "critical"
    assert risk >= 85
    assert len(findings) > 0

def test_detects_removed_retry():
    diff = """
- retry_count = 3
+ pass
"""

    risk, status, findings = heuristic_scan(diff)

    assert status in ["warning", "critical"]
    assert any("retry" in f.lower() for f in findings)


def test_detects_removed_error_handling():
    diff = """
- try:
-     do_payment()
- except Exception:
-     handle_error()
+ do_payment()
"""

    risk, status, findings = heuristic_scan(diff)

    assert status == "warning"
    assert any("error handling" in f.lower() for f in findings)


def test_schema_change_detection():
    diff = """
+ ALTER TABLE users DROP COLUMN password
"""

    risk, status, findings = heuristic_scan(diff)

    assert status in ["warning", "critical"]
    assert len(findings) > 0


def test_run_returns_agent_result():
    diff = "+ print('hello')"

    result = asyncio.run(run(diff, ["test.py"]))

    assert result.agent_name == "Diff Analyst"
    assert result.status in ["pass", "warning", "critical"]
    assert isinstance(result.findings, list)


# ── run() public coroutine tests ────────────────────────────────────


class TestRunPublicInterface:
    def test_empty_diff_returns_warning(self):
        result = asyncio.run(run("", []))

        assert result.agent_name == "Diff Analyst"
        assert result.status == "warning"
        assert any("No diff content" in f for f in result.findings)

    def test_whitespace_only_diff_returns_warning(self):
        result = asyncio.run(run("   \n  ", []))

        assert result.agent_name == "Diff Analyst"
        assert result.status == "warning"
        assert any("No diff content" in f for f in result.findings)

    def test_error_handling_removal_flagged_via_run(self):
        diff = dedent("""\
            - try:
            -     risky_op()
            - except Exception:
            -     handle_err()
            + risky_op()
        """)

        result = asyncio.run(run(diff, ["service.py"]))

        assert result.status == "warning"
        assert any("error handling" in f.lower() for f in result.findings)

    @patch("agents.diff_analyst.diff_agent.call_llm")
    def test_llm_path_parses_valid_json(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "risk_score_modifier": 30,
            "status": "warning",
            "findings": ["LLM found a risk"],
            "recommended_action": "Review before merge.",
        })
        env_vars = {
            "AZURE_OPENAI_ENDPOINT": "https://fake.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "fake-key",
            "AZURE_OPENAI_DEPLOYMENT": "fake-deployment",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            result = asyncio.run(run("+ new line", ["file.py"]))

        assert result.agent_name == "Diff Analyst"
        assert result.status == "warning"
        assert any("LLM found" in f for f in result.findings)

    @patch("agents.diff_analyst.diff_agent.call_llm")
    def test_llm_path_handles_invalid_json_gracefully(self, mock_llm):
        mock_llm.return_value = "not valid json at all"
        env_vars = {
            "AZURE_OPENAI_ENDPOINT": "https://fake.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "fake-key",
            "AZURE_OPENAI_DEPLOYMENT": "fake-deployment",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            result = asyncio.run(run("+ harmless line", ["file.py"]))

        assert result.agent_name == "Diff Analyst"
        assert result.status in ("warning", "critical")

    def test_changed_files_list_does_not_break_run(self):
        diff = "+ print('hello world')"

        result = asyncio.run(run(diff, ["src/app.py", "tests/test_app.py"]))

        assert result.agent_name == "Diff Analyst"
        assert result.status in ("pass", "warning", "critical")

    def test_data_contract_compliance(self):
        from agents.shared.data_contract import AgentResult

        diff = "- old line\n+ new line"
        result = asyncio.run(run(diff, []))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "Diff Analyst"
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")
        assert isinstance(result.findings, list)
        assert isinstance(result.recommended_action, str)
        # Round-trip through JSON serialization must be lossless
        parsed = AgentResult.from_json(result.to_json())
        assert parsed == result