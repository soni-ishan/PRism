"""Tests for the Coverage Agent run() function.

This module replaces the deleted manual test script ``test_my_agent.py`` with
proper pytest tests.  All external I/O (GitHub API calls via httpx) is fully
mocked so the suite can run offline without credentials.

Test scenarios:
  TestCoverageAgentRun            — core run() behaviour
  TestCoverageAgentCommentContent — autofix comment body assertions
  TestCoverageAgentDeduplication  — duplicate-comment prevention
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agents.coverage_agent import run

# Risk score increments that mirror the constants in agents/coverage_agent/__init__.py
_RISK_PER_MISSING_TEST = 15   # a source file whose expected test path returns 404
_RISK_PER_DELETED_TEST = 25   # a tests/test_*.py file with status == "removed"
_RISK_API_ERROR = 50          # fallback when the GitHub API is unreachable / token missing


# ── Shared mock helpers ────────────────────────────────────────────────────────


class MockResponse:
    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def json(self):
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class MockAsyncClient:
    """Minimal async HTTP client that routes requests through pre-registered fixtures."""

    def __init__(
        self,
        get_routes: dict[str, MockResponse],
        post_routes: dict[str, MockResponse] | None = None,
        **_kwargs,
    ):
        self._get_routes = get_routes
        self._post_routes = post_routes or {}
        self.post_calls: list[tuple[str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **_kwargs):
        if url not in self._get_routes:
            raise Exception(f"Unexpected GET {url}")
        return self._get_routes[url]

    async def post(self, url: str, json=None, **_kwargs):
        self.post_calls.append((url, json))
        if url not in self._post_routes:
            return MockResponse(201, {"id": 1})
        return self._post_routes[url]


@pytest.fixture(autouse=True)
def _github_token(monkeypatch):
    """Inject a fake GitHub PAT so the agent doesn't bail out immediately."""
    monkeypatch.setenv("GH_PAT", "fake-test-token")


# ── Core run() behaviour ───────────────────────────────────────────────────────


class TestCoverageAgentRun:
    """Core scenarios for agents.coverage_agent.run()."""

    @pytest.mark.asyncio
    async def test_clean_pr_returns_pass_with_zero_risk(self):
        """A PR whose changed files all have corresponding tests should pass."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/1/files": MockResponse(
                200,
                [{"filename": "agents/coverage_agent/__init__.py", "status": "modified"}],
            ),
            f"https://api.github.com/repos/{repo}/contents/tests/test_coverage_agent.py": MockResponse(200, {}),
        }

        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=MockAsyncClient(get_routes)):
            result = await run(pr_number=1, repo=repo)

        assert result.status == "pass"
        assert result.risk_score_modifier == 0
        assert result.agent_name == "Coverage Agent"

    @pytest.mark.asyncio
    async def test_missing_test_file_increments_risk(self):
        """A source file without a test file should add 15 to the risk score."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/2/files": MockResponse(
                200,
                [{"filename": "agents/new_feature/agent.py", "status": "added"}],
            ),
            f"https://api.github.com/repos/{repo}/contents/tests/test_agent.py": MockResponse(404, {}),
            f"https://api.github.com/repos/{repo}/pulls/2": MockResponse(
                200, {"head": {"ref": "feature/new-feature"}}
            ),
            f"https://api.github.com/repos/{repo}/issues/2/comments": MockResponse(200, []),
        }

        mock_client = MockAsyncClient(get_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            result = await run(pr_number=2, repo=repo)

        assert result.risk_score_modifier == _RISK_PER_MISSING_TEST
        assert any("No test file found" in f for f in result.findings)

    @pytest.mark.asyncio
    async def test_deleted_test_file_increments_risk_by_25(self):
        """Removing a test file should add 25 to the risk score."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/3/files": MockResponse(
                200,
                [{"filename": "tests/test_math_utils.py", "status": "removed"}],
            ),
            f"https://api.github.com/repos/{repo}/pulls/3": MockResponse(
                200, {"head": {"ref": "refactor/cleanup"}}
            ),
            f"https://api.github.com/repos/{repo}/issues/3/comments": MockResponse(200, []),
        }

        mock_client = MockAsyncClient(get_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            result = await run(pr_number=3, repo=repo)

        assert result.risk_score_modifier == _RISK_PER_DELETED_TEST
        assert any("Deleted test file" in f for f in result.findings)

    @pytest.mark.asyncio
    async def test_missing_env_token_returns_graceful_warning(self):
        """If GH_PAT is not set the agent should return a warning, not raise."""
        repo = "devDays/PRism"
        with patch.dict(os.environ, {}, clear=True):
            result = await run(pr_number=1, repo=repo)

        assert result.status == "warning"
        assert result.risk_score_modifier == _RISK_API_ERROR
        assert any("failed" in f.lower() for f in result.findings)

    @pytest.mark.asyncio
    async def test_github_api_error_returns_graceful_warning(self):
        """An unexpected HTTP error from the GitHub API should not crash the agent."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/4/files": MockResponse(
                503, {"message": "Service Unavailable"}
            ),
        }

        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=MockAsyncClient(get_routes)):
            result = await run(pr_number=4, repo=repo)

        assert result.status == "warning"
        assert result.risk_score_modifier == _RISK_API_ERROR

    @pytest.mark.asyncio
    async def test_non_python_files_are_ignored(self):
        """Non-Python files (YAML, JS, etc.) should not trigger a coverage check."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/5/files": MockResponse(
                200,
                [
                    {"filename": ".github/workflows/ci.yml", "status": "modified"},
                    {"filename": "README.md", "status": "modified"},
                    {"filename": "vscode_extension/src/extension.ts", "status": "modified"},
                ],
            ),
        }

        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=MockAsyncClient(get_routes)):
            result = await run(pr_number=5, repo=repo)

        assert result.status == "pass"
        assert result.risk_score_modifier == 0

    @pytest.mark.asyncio
    async def test_skip_autofix_suppresses_pr_comment(self):
        """With skip_autofix=True no POST comment should be sent even when tests are missing."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/6/files": MockResponse(
                200,
                [{"filename": "agents/coverage_agent/__init__.py", "status": "modified"}],
            ),
            f"https://api.github.com/repos/{repo}/contents/tests/test_coverage_agent.py": MockResponse(404, {}),
        }

        mock_client = MockAsyncClient(get_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            result = await run(pr_number=6, repo=repo, skip_autofix=True)

        assert result.risk_score_modifier == _RISK_PER_MISSING_TEST
        assert len(mock_client.post_calls) == 0

    @pytest.mark.asyncio
    async def test_risk_score_capped_at_100(self):
        """Risk score must never exceed 100 regardless of how many files are missing tests."""
        repo = "devDays/PRism"
        n = 20  # 20 × 15 = 300 → capped at 100
        changed = [{"filename": f"agents/mod_{i}.py", "status": "added"} for i in range(n)]
        get_routes: dict[str, MockResponse] = {
            f"https://api.github.com/repos/{repo}/pulls/7/files": MockResponse(200, changed),
        }
        for i in range(n):
            get_routes[f"https://api.github.com/repos/{repo}/contents/tests/test_mod_{i}.py"] = MockResponse(404, {})
        get_routes[f"https://api.github.com/repos/{repo}/pulls/7"] = MockResponse(
            200, {"head": {"ref": "chore/big-refactor"}}
        )
        get_routes[f"https://api.github.com/repos/{repo}/issues/7/comments"] = MockResponse(200, [])

        mock_client = MockAsyncClient(get_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            result = await run(pr_number=7, repo=repo)

        assert result.risk_score_modifier == 100


# ── Autofix comment content ────────────────────────────────────────────────────


class TestCoverageAgentCommentContent:
    """Verify the body of the autofix comment posted to the PR."""

    @pytest.mark.asyncio
    async def test_comment_mentions_copilot_and_expected_test_path(self):
        """The autofix comment must mention @copilot and list the expected test path."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/10/files": MockResponse(
                200,
                [{"filename": "agents/timing_agent/__init__.py", "status": "modified"}],
            ),
            f"https://api.github.com/repos/{repo}/contents/tests/test_timing_agent.py": MockResponse(404, {}),
            f"https://api.github.com/repos/{repo}/pulls/10": MockResponse(
                200, {"head": {"ref": "feature/timing"}}
            ),
            f"https://api.github.com/repos/{repo}/issues/10/comments": MockResponse(200, []),
        }
        post_routes = {
            f"https://api.github.com/repos/{repo}/issues/10/comments": MockResponse(201, {"id": 42}),
        }

        mock_client = MockAsyncClient(get_routes, post_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            await run(pr_number=10, repo=repo)

        assert len(mock_client.post_calls) == 1
        body: str = mock_client.post_calls[0][1]["body"]
        assert "@copilot" in body
        assert "tests/test_timing_agent.py" in body

    @pytest.mark.asyncio
    async def test_comment_lists_deleted_test_file(self):
        """Deleted test file paths should appear verbatim in the autofix comment."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/11/files": MockResponse(
                200,
                [{"filename": "tests/test_history_agent.py", "status": "removed"}],
            ),
            f"https://api.github.com/repos/{repo}/pulls/11": MockResponse(
                200, {"head": {"ref": "refactor/history"}}
            ),
            f"https://api.github.com/repos/{repo}/issues/11/comments": MockResponse(200, []),
        }
        post_routes = {
            f"https://api.github.com/repos/{repo}/issues/11/comments": MockResponse(201, {"id": 43}),
        }

        mock_client = MockAsyncClient(get_routes, post_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            await run(pr_number=11, repo=repo)

        assert len(mock_client.post_calls) == 1
        body: str = mock_client.post_calls[0][1]["body"]
        assert "tests/test_history_agent.py" in body


# ── Duplicate-comment prevention ──────────────────────────────────────────────


class TestCoverageAgentDeduplication:
    """Verify that the agent never posts a duplicate Coverage Analysis comment."""

    @pytest.mark.asyncio
    async def test_no_duplicate_comment_when_marker_already_present(self):
        """If the PR already has a [PRism] Coverage Analysis comment, skip posting."""
        repo = "devDays/PRism"
        existing_comments = [
            {"body": "### [PRism] Coverage Analysis\n\nAlready posted."},
        ]
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/20/files": MockResponse(
                200,
                [{"filename": "agents/coverage_agent/__init__.py", "status": "modified"}],
            ),
            f"https://api.github.com/repos/{repo}/contents/tests/test_coverage_agent.py": MockResponse(404, {}),
            f"https://api.github.com/repos/{repo}/pulls/20": MockResponse(
                200, {"head": {"ref": "feature/dedup"}}
            ),
            f"https://api.github.com/repos/{repo}/issues/20/comments": MockResponse(
                200, existing_comments
            ),
        }

        mock_client = MockAsyncClient(get_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            await run(pr_number=20, repo=repo)

        assert len(mock_client.post_calls) == 0

    @pytest.mark.asyncio
    async def test_comment_is_posted_when_no_prior_marker(self):
        """A first-time run with missing tests should always post exactly one comment."""
        repo = "devDays/PRism"
        get_routes = {
            f"https://api.github.com/repos/{repo}/pulls/21/files": MockResponse(
                200,
                [{"filename": "agents/coverage_agent/__init__.py", "status": "modified"}],
            ),
            f"https://api.github.com/repos/{repo}/contents/tests/test_coverage_agent.py": MockResponse(404, {}),
            f"https://api.github.com/repos/{repo}/pulls/21": MockResponse(
                200, {"head": {"ref": "feature/first-run"}}
            ),
            f"https://api.github.com/repos/{repo}/issues/21/comments": MockResponse(200, []),
        }

        mock_client = MockAsyncClient(get_routes)
        with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
            await run(pr_number=21, repo=repo)

        assert len(mock_client.post_calls) == 1
