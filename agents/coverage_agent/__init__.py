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

# The standard label that triggers GitHub Copilot Workspace
# Ensure this label exists in your repo or is auto-created.
COPILOT_TRIGGER_LABEL = "copilot-issue-agent"


def _expected_test_path(source_path: str) -> str:
    """Map a source path to a conventional test path."""
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


async def _issue_already_exists(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
) -> bool:
    """Return True if an open autofix issue for this PR already exists."""
    url = f"https://api.github.com/repos/{repo}/issues"
    try:
        resp = await client.get(url, params={"state": "open", "per_page": 50})
        if resp.is_error:
            return False
        title = f"[PRism] Generate missing tests for PR #{pr_number}"
        return any(issue.get("title") == title for issue in resp.json())
    except Exception:
        return False


async def _create_autofix_issue(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
    files_needing_tests: list[str],
    pr_branch: str,
) -> None:
    """Create a GitHub issue and trigger the Copilot coding agent.

    Using the 'copilot-issue-agent' label is the current standard for 
    triggering the Copilot Workspace agent to begin a task.
    """
    if not files_needing_tests:
        return

    if await _issue_already_exists(client, repo, pr_number):
        return

    source_files = [f for f in files_needing_tests if not f.startswith("tests/")]
    deleted_test_files = [f for f in files_needing_tests if f.startswith("tests/")]
    expected_tests = [_expected_test_path(f) for f in source_files] + deleted_test_files

    files_list = "\n".join(f"- `{path}`" for path in files_needing_tests)
    tests_list = "\n".join(f"- `{path}`" for path in expected_tests)

    body = f"""\
## Task: Generate Missing Tests for PR #{pr_number}

@github-copilot The following files changed in PR #{pr_number} (branch: `{pr_branch}`) are missing test coverage.

### Files that need tests
{files_list}

### Expected test file paths
{tests_list}

### Instructions
1. Check out branch `{pr_branch}` as the base for your changes.
2. Create each missing test file at the path shown above.
3. Write comprehensive unit tests using `pytest` and `pytest-asyncio`.
4. Mock all external I/O (GitHub API, HTTP requests).
5. Open a new pull request targeting `{pr_branch}`.

### Context
Repository: **PRism**
"""

    issue_url = f"https://api.github.com/repos/{repo}/issues"
    issue_payload = {
        "title": f"[PRism] Generate missing tests for PR #{pr_number}",
        "body": body,
        # We use the Label trigger as it's more reliable for the Copilot Bot
        "labels": [COPILOT_TRIGGER_LABEL],
    }

    try:
        resp = await client.post(issue_url, json=issue_payload)
        if resp.is_error:
            logger.warning("Failed to create issue (HTTP %d): %s", resp.status_code, resp.text[:200])
            return

        issue_data = resp.json()
        issue_number = issue_data.get("number")
        
        # Verify if assignment worked (some repos allow 'copilot' as an assignee, some don't)
        # We try to add the assignee in a separate step if the label alone isn't enough
        assign_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/assignees"
        await client.post(assign_url, json={"assignees": ["github-copilot"]})

        logger.info("Created autofix issue #%d and tagged for Copilot.", issue_number)
    except Exception as exc:
        logger.warning("Autofix issue creation failed: %s", exc)


async def run(pr_number: int, repo: str, skip_autofix: bool = False) -> AgentResult:
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

            risk_score = min(risk_score, 100)

            if risk_score >= 15 and not skip_autofix:
                pr_branch = await _get_pr_branch(client, repo, pr_number)
                await _create_autofix_issue(client, repo, pr_number, files_needing_tests, pr_branch)

            status = "pass" if risk_score <= 20 else "warning" if risk_score <= 50 else "critical"
            recommended_action = "Coverage checks passed." if status == "pass" else "Add tests before merging."

            return AgentResult(
                agent_name=AGENT_NAME,
                risk_score_modifier=risk_score,
                status=status,
                findings=findings or ["All files have tests."],
                recommended_action=recommended_action,
            )

    except Exception as exc:
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=50,
            status="warning",
            findings=[f"Coverage API check failed: {exc}"],
            recommended_action="Manual coverage verification required.",
        )