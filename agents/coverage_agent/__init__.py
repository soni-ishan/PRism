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


async def _get_pr_branch(client: httpx.AsyncClient, repo: str, pr_number: int) -> str:
    """Fetch the head branch name of a pull request."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.json().get("head", {}).get("ref", "unknown")
    except Exception:
        return "unknown"


async def _create_autofix_issue(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
    files_needing_tests: list[str],
    pr_branch: str,
) -> None:
    """Create a GitHub issue and assign it to the Copilot coding agent.

    Assigning to ``copilot`` is what **actually triggers** GitHub Copilot
    coding agent to start working.  The agent will:

    1. Check out ``pr_branch``.
    2. Write the missing test files.
    3. Open a new pull request with the generated tests.
    """
    if not files_needing_tests:
        return

    # Build expected test paths: source files map to conventional test paths;
    # deleted test files are included verbatim (they need to be restored).
    source_files = [f for f in files_needing_tests if not f.startswith("tests/")]
    deleted_test_files = [f for f in files_needing_tests if f.startswith("tests/")]
    expected_tests = [_expected_test_path(f) for f in source_files] + deleted_test_files

    files_list = "\n".join(f"- `{path}`" for path in files_needing_tests)
    tests_list = "\n".join(f"- `{path}`" for path in expected_tests)

    body = f"""\
## Task: Generate Missing Tests for PR #{pr_number}

@github-copilot The following files changed in PR #{pr_number} (branch: `{pr_branch}`) \
are missing test coverage — either source files with no corresponding test, or test \
files that were deleted and need to be restored.

### Files that need tests
{files_list}

### Expected test file paths
{tests_list}

### Instructions
1. Check out branch `{pr_branch}` as the base for your changes.
2. Create each missing test file at the path shown above under **Expected test file paths**.
3. Write comprehensive unit tests using `pytest` and `pytest-asyncio` for async functions.
4. Mock all external I/O (GitHub API calls, Azure SDK calls, HTTP requests) with \
`unittest.mock.patch` or `pytest-mock`.
5. Each test module should import the corresponding source module and cover the public \
`run()` function plus key helpers.
6. Open a new pull request targeting the same base branch as PR #{pr_number} with a \
title like `tests: add coverage for PR #{pr_number}`.

### Context
This repository is **PRism** — a multi-agent AI deployment risk pipeline. \
Each agent exposes an async `run()` function as its public API. \
Shared types live in `agents/shared/data_contract.py` (`AgentResult`, `VerdictReport`)."""

    issue_url = f"https://api.github.com/repos/{repo}/issues"
    issue_payload = {
        "title": f"[PRism] Generate missing tests for PR #{pr_number}",
        "body": body,
    }

    try:
        # Step 1: Create the issue — best-effort, must not crash the pipeline.
        resp = await client.post(issue_url, json=issue_payload)
        if resp.is_error:
            return

        issue_number = resp.json().get("number")
        if not issue_number:
            return

        # Step 2: Assign to the GitHub Copilot coding agent.
        # This is the call that *triggers* Copilot to start working on the task.
        assignees_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/assignees"
        await client.post(assignees_url, json={"assignees": ["copilot"]})
    except Exception:  # noqa: BLE001 - autofix is best-effort, must not corrupt coverage score
        pass


async def run(pr_number: int, repo: str) -> AgentResult:
    """Run coverage risk checks for a pull request."""
    findings: list[str] = []
    files_needing_tests: list[str] = []
    risk_score = 0

    try:
        token = os.environ["GH_PAT"]
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
                pr_branch = await _get_pr_branch(client, repo, pr_number)
                await _create_autofix_issue(
                    client, repo, pr_number, files_needing_tests, pr_branch
                )

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