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

# Marker embedded in the autofix PR comment for deduplication
_COMMENT_MARKER = "<!-- prism-coverage-autofix -->"


def _test_module_name(source_path: str) -> str:
    """Derive the test module name (without prefix/suffix) for a source file."""
    p = PurePosixPath(source_path)
    return p.parent.name if p.name == "__init__.py" else p.stem


def _expected_test_path(source_path: str) -> str:
    """Map a source path to a conventional test path."""
    return f"tests/test_{_test_module_name(source_path)}.py"


def _candidate_test_paths(source_path: str) -> list[str]:
    """Return all conventional test paths for a source file.

    Checks both the top-level ``tests/`` directory and the ``tests/unit/``
    subdirectory so that tests organised in either layout are recognised.
    """
    name = _test_module_name(source_path)
    return [f"tests/test_{name}.py", f"tests/unit/test_{name}.py"]


async def _comment_already_posted(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
) -> bool:
    """Return True if PRism has already posted an autofix comment on this PR."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        resp = await client.get(url, params={"per_page": 50})
        if resp.is_error:
            return False
        return any(_COMMENT_MARKER in (c.get("body") or "") for c in resp.json())
    except Exception:
        return False


async def _post_copilot_pr_comment(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
    files_needing_tests: list[str],
) -> None:
    """Post a PR comment mentioning @copilot to trigger automatic test generation.

    Uses an invisible HTML marker for deduplication so only one comment is ever
    posted per PR run.
    """
    if not files_needing_tests:
        return

    if await _comment_already_posted(client, repo, pr_number):
        logger.info("Autofix comment already posted for PR #%d, skipping.", pr_number)
        return

    files_list = "\n".join(f"- `{path}`" for path in files_needing_tests)
    source_files = [f for f in files_needing_tests if not f.startswith("tests/")]
    deleted_test_files = [f for f in files_needing_tests if f.startswith("tests/")]
    expected_tests = [_expected_test_path(f) for f in source_files] + deleted_test_files
    tests_list = "\n".join(f"- `{path}`" for path in expected_tests)

    body = f"""\
{_COMMENT_MARKER}
@copilot The following files changed in this PR are missing test coverage. Please generate the missing tests.

### Files that need tests
{files_list}

### Expected test file paths
{tests_list}

### Instructions
1. Create each missing test file at the path shown above.
2. Write comprehensive unit tests using `pytest` and `pytest-asyncio`.
3. Mock all external I/O (GitHub API, HTTP requests).
"""

    comments_url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        resp = await client.post(comments_url, json={"body": body})
        if resp.is_error:
            logger.warning("Failed to post autofix comment (HTTP %d)", resp.status_code)
            return
        logger.info("Posted @copilot autofix comment on PR #%d", pr_number)
    except Exception as exc:
        logger.warning("Autofix comment failed: %s", exc)


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

                test_exists = False
                for test_candidate in _candidate_test_paths(filename):
                    contents_url = f"https://api.github.com/repos/{repo}/contents/{test_candidate}"
                    test_response = await client.get(contents_url)
                    if test_response.status_code != 404:
                        test_exists = True
                        break

                if not test_exists:
                    risk_score += 15
                    findings.append(f"No test file found for {filename}")
                    files_needing_tests.append(filename)

            risk_score = min(risk_score, 100)

            if risk_score >= 15 and not skip_autofix:
                await _post_copilot_pr_comment(client, repo, pr_number, files_needing_tests)

            status = "pass" if risk_score <= 20 else "warning" if risk_score <= 50 else "critical"
            recommended_action = "Coverage checks passed." if status == "pass" else "Add tests before merging."

            return AgentResult(
                agent_name=AGENT_NAME,
                risk_score_modifier=risk_score,
                status=status,
                findings=findings or ["All changed Python files have corresponding tests."],
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