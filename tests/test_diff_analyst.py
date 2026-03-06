from agents.diff_analyst.diff_agent import heuristic_scan, run
from textwrap import dedent
import asyncio

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
    print("RISK:", risk)
    print("STATUS:", status)
    print("FINDINGS:", findings)

    assert status in ["warning", "critical"]
    assert any("retry" in f.lower() for f in findings)

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