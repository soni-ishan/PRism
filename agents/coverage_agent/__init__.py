"""PRism Coverage Agent.

Evaluates coverage risk for PR files by checking whether changed Python source files
have corresponding tests and whether tests were removed.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath

import httpx

from agents.shared.data_contract import AgentResult

AGENT_NAME = "Coverage Agent"


def _expected_test_path(source_path: str) -> str:
    """Map a source path to a conventional test path.

    Examples:
    - agents/timing_agent/__init__.py -> tests/test_timing_agent.py
    - agents/foo/bar.py -> tests/test_bar.py
    """
    p = PurePosixPath(source_path)
    if p.name == "__init__.py":
        target_name = p.parent.name
    else:
        target_name = p.stem
    return f"tests/test_{target_name}.py"


async def _create_autofix_issue(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
    files_needing_tests: list[str],
) -> None:
    """Create a GitHub issue asking Copilot to generate missing tests."""
    if not files_needing_tests:
        return

    issue_url = f"https://api.github.com/repos/{repo}/issues"
    body_lines = [
        "Please generate tests for the following files:",
        "",
        *[f"- {path}" for path in files_needing_tests],
    ]
    payload = {
        "title": f"PRism: Auto-generate tests for PR #{pr_number}",
        "body": "\n".join(body_lines),
    }
    # Best-effort only; coverage analysis should not fail if issue creation fails.
    await client.post(issue_url, json=payload)


async def run(pr_number: int, repo: str) -> AgentResult:
    """Run coverage risk checks for a pull request."""
    findings: list[str] = []
    files_needing_tests: list[str] = []
    risk_score = 0

    try:
        token = os.environ["GITHUB_TOKEN"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

        async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
            files_url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files"
            response = await client.get(files_url)
            response.raise_for_status()
            changed_files = response.json()

            for changed in changed_files:
                filename = str(changed.get("filename", ""))
                file_status = str(changed.get("status", ""))

                if not filename:
                    continue

                # Removed tests are a direct regression signal.
                if filename.startswith("tests/test_") and file_status == "removed":
                    risk_score += 25
                    findings.append(f"Deleted test file: {filename}")
                    files_needing_tests.append(filename)
                    continue

                # Only evaluate Python source files outside the tests folder.
                if not filename.endswith(".py") or filename.startswith("tests/"):
                    continue

                expected_test = _expected_test_path(filename)
                contents_url = f"https://api.github.com/repos/{repo}/contents/{expected_test}"
                test_response = await client.get(contents_url)

                if test_response.status_code == 404:
                    risk_score += 15
                    findings.append(f"No test file found for {filename}")
                    files_needing_tests.append(filename)
                elif test_response.is_error:
                    test_response.raise_for_status()

            risk_score = min(risk_score, 100)

            # Trigger Copilot autofix from 15+ risk so a single missing test
            # still creates an issue while preserving pass/warning/critical bands.
            if risk_score >= 15:
                await _create_autofix_issue(client, repo, pr_number, files_needing_tests)

            if risk_score <= 20:
                status = "pass"
                recommended_action = "Coverage checks passed. Proceed with PR review."
            elif risk_score <= 50:
                status = "warning"
                recommended_action = "Add or restore missing tests before merging."
            else:
                status = "critical"
                recommended_action = "Block merge until missing/deleted tests are addressed."

            if not findings:
                findings.append("All changed Python files have corresponding tests.")

            return AgentResult(
                agent_name=AGENT_NAME,
                risk_score_modifier=risk_score,
                status=status,
                findings=findings,
                recommended_action=recommended_action,
            )

    except Exception as exc:  # noqa: BLE001 - return warning fallback by design
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=50,
            status="warning",
            findings=[f"Coverage API check failed: {exc}"],
            recommended_action="Manual coverage verification required due to API failure.",
        )