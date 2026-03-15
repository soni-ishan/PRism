"""PRism Coverage Agent.

Evaluates coverage risk for PR files by checking whether changed Python source files
have corresponding tests and whether tests were removed.
"""

from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath

import httpx

from agents.shared.data_contract import AgentResult

logger = logging.getLogger("prism.coverage")
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


async def _comment_already_exists(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
) -> bool:
    """Return True if PRism has already commented on this PR about missing tests."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        resp = await client.get(url, params={"per_page": 100})
        if resp.is_error:
            return False
        # Look for the unique PRism header in existing comments
        marker = "### [PRism] Coverage Analysis"
        return any(marker in comment.get("body", "") for comment in resp.json())
    except Exception:
        return False


async def _post_autofix_comment(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
    files_needing_tests: list[str],
    pr_branch: str,
) -> None:
    """Post a comment on the PR to trigger Copilot.

    Mentioning @copilot in a PR comment is a common
    trigger for the agent to analyze the PR and suggest fixes.
    """
    if not files_needing_tests:
        return

    # Deduplication: don't spam the PR with the same comment.
    if await _comment_already_exists(client, repo, pr_number):
        return

    source_files = [f for f in files_needing_tests if not f.startswith("tests/")]
    deleted_test_files = [f for f in files_needing_tests if f.startswith("tests/")]
    expected_tests = [_expected_test_path(f) for f in source_files] + deleted_test_files

    files_list = "\n".join(f"- `{path}`" for path in files_needing_tests)
    tests_list = "\n".join(f"- `{path}`" for path in expected_tests)

    body = f"""\
### [PRism] Coverage Analysis

@copilot The PRism deployment analysis has identified missing test coverage in this PR.

**Files needing tests:**
{files_list}

**Expected test paths:**
{tests_list}

**Instructions:**
Please see the analysis above and generate the missing `pytest` modules. Ensure you mock external I/O and cover the public `run()` function for each agent module.
"""

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        resp = await client.post(url, json={"body": body})
        if resp.is_error:
            logger.warning("Failed to post PR comment (HTTP %d): %s", resp.status_code, resp.text[:200])
        else:
            logger.info("Posted coverage analysis comment to PR #%d", pr_number)
    except Exception as exc:
        logger.warning("Failed to post PR comment: %s", exc)


async def run(pr_number: int, repo: str, skip_autofix: bool = False, gh_token: str | None = None) -> AgentResult:
    """Run coverage risk checks for a pull request."""
    findings: list[str] = []
    files_needing_tests: list[str] = []
    risk_score = 0

    try:
        token = gh_token or os.environ.get("GH_PAT", "")
        if not token:
            raise RuntimeError("No GitHub token available (gh_token param and GH_PAT env var both empty)")
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

                if filename.startswith("tests/test_") and file_status == "removed":
                    risk_score += 25
                    findings.append(f"Deleted test file: {filename}")
                    files_needing_tests.append(filename)
                    continue

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

            # Trigger Copilot via PR comment if risk is detected.
            if risk_score >= 15 and not skip_autofix:
                pr_branch = await _get_pr_branch(client, repo, pr_number)
                await _post_autofix_comment(
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

    except Exception as exc:
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=50,
            status="warning",
            findings=[f"Coverage API check failed: {exc}"],
            recommended_action="Manual coverage verification required due to API failure.",
        )