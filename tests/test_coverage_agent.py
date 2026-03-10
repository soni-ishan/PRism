from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agents.coverage_agent import run


class MockResponse:
    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json_data = json_data

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def json(self):
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class MockAsyncClient:
    def __init__(self, get_routes: dict[str, MockResponse], post_routes: dict[str, MockResponse] | None = None, **_kwargs):
        self._get_routes = get_routes
        self._post_routes = post_routes or {}
        self.post_calls: list[tuple[str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        if url not in self._get_routes:
            raise Exception(f"Unexpected GET {url}")
        return self._get_routes[url]

    async def post(self, url: str, json=None):
        self.post_calls.append((url, json))
        if url not in self._post_routes:
            return MockResponse(201, {"ok": True})
        return self._post_routes[url]


@pytest.fixture(autouse=True)
def github_token_env():
    with patch.dict(os.environ, {"GH_PAT": "test-token"}, clear=False):
        yield


@pytest.mark.asyncio
async def test_all_files_have_tests_pass_status():
    repo = "devDays/PRism"
    get_routes = {
        f"https://api.github.com/repos/{repo}/pulls/1/files": MockResponse(
            200,
            [
                {"filename": "agents/timing_agent/__init__.py", "status": "modified"},
                {"filename": "agents/verdict_agent/__init__.py", "status": "modified"},
            ],
        ),
        f"https://api.github.com/repos/{repo}/contents/tests/test_timing_agent.py": MockResponse(200, {}),
        f"https://api.github.com/repos/{repo}/contents/tests/test_verdict_agent.py": MockResponse(200, {}),
    }

    with patch("agents.coverage_agent.httpx.AsyncClient", return_value=MockAsyncClient(get_routes)):
        result = await run(pr_number=1, repo=repo)

    assert result.status == "pass"
    assert result.risk_score_modifier == 0
    assert result.agent_name == "Coverage Agent"


@pytest.mark.asyncio
async def test_some_files_missing_tests_warning_status():
    repo = "devDays/PRism"
    get_routes = {
        f"https://api.github.com/repos/{repo}/pulls/2/files": MockResponse(
            200,
            [
                {"filename": "agents/diff_analyst/diff_agent.py", "status": "modified"},
                {"filename": "agents/history_agent/agent.py", "status": "modified"},
                {"filename": "agents/orchestrator/server.py", "status": "modified"},
            ],
        ),
        f"https://api.github.com/repos/{repo}/contents/tests/test_diff_agent.py": MockResponse(200, {}),
        f"https://api.github.com/repos/{repo}/contents/tests/test_agent.py": MockResponse(404, {}),
        f"https://api.github.com/repos/{repo}/contents/tests/test_server.py": MockResponse(404, {}),
    }
    post_routes = {
        f"https://api.github.com/repos/{repo}/issues": MockResponse(201, {"number": 123}),
    }

    mock_client = MockAsyncClient(get_routes, post_routes)
    with patch(
        "agents.coverage_agent.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await run(pr_number=2, repo=repo)

    assert result.status == "warning"
    assert result.risk_score_modifier == 30
    assert any("No test file found" in finding for finding in result.findings)
    assert len(mock_client.post_calls) == 1
    assert mock_client.post_calls[0][0].endswith(f"/repos/{repo}/issues")


@pytest.mark.asyncio
async def test_many_files_missing_tests_critical_status():
    repo = "devDays/PRism"
    changed_files = [
        {"filename": "agents/a.py", "status": "modified"},
        {"filename": "agents/b.py", "status": "modified"},
        {"filename": "agents/c.py", "status": "modified"},
        {"filename": "agents/d.py", "status": "modified"},
    ]
    get_routes = {
        f"https://api.github.com/repos/{repo}/pulls/3/files": MockResponse(200, changed_files),
        f"https://api.github.com/repos/{repo}/contents/tests/test_a.py": MockResponse(404, {}),
        f"https://api.github.com/repos/{repo}/contents/tests/test_b.py": MockResponse(404, {}),
        f"https://api.github.com/repos/{repo}/contents/tests/test_c.py": MockResponse(404, {}),
        f"https://api.github.com/repos/{repo}/contents/tests/test_d.py": MockResponse(404, {}),
    }
    post_routes = {
        f"https://api.github.com/repos/{repo}/issues": MockResponse(201, {"number": 321}),
    }

    mock_client = MockAsyncClient(get_routes, post_routes)
    with patch(
        "agents.coverage_agent.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await run(pr_number=3, repo=repo)

    assert result.status == "critical"
    assert result.risk_score_modifier == 60
    assert len(mock_client.post_calls) == 1


@pytest.mark.asyncio
async def test_single_missing_test_file_triggers_issue_creation():
    repo = "devDays/PRism"
    get_routes = {
        f"https://api.github.com/repos/{repo}/pulls/5/files": MockResponse(
            200,
            [{"filename": "agents/coverage_agent/__init__.py", "status": "modified"}],
        ),
        f"https://api.github.com/repos/{repo}/contents/tests/test_coverage_agent.py": MockResponse(404, {}),
    }

    mock_client = MockAsyncClient(get_routes)
    with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
        result = await run(pr_number=5, repo=repo)

    # Risk is 15 for one missing test file, and this now triggers issue creation.
    assert result.status == "pass"
    assert result.risk_score_modifier == 15
    assert len(mock_client.post_calls) == 1
    assert mock_client.post_calls[0][0].endswith(f"/repos/{repo}/issues")


@pytest.mark.asyncio
async def test_removed_test_file_triggers_warning_and_issue_creation():
    repo = "devDays/PRism"
    get_routes = {
        f"https://api.github.com/repos/{repo}/pulls/6/files": MockResponse(
            200,
            [{"filename": "tests/test_orchestrator.py", "status": "removed"}],
        ),
    }

    mock_client = MockAsyncClient(get_routes)
    with patch("agents.coverage_agent.httpx.AsyncClient", return_value=mock_client):
        result = await run(pr_number=6, repo=repo)

    assert result.status == "warning"
    assert result.risk_score_modifier == 25
    assert len(mock_client.post_calls) == 1


@pytest.mark.asyncio
async def test_missing_github_token_returns_fallback_and_does_not_trigger_issue_creation():
    repo = "devDays/PRism"

    with patch.dict(os.environ, {}, clear=True):
        result = await run(pr_number=7, repo=repo)

    assert result.status == "warning"
    assert result.risk_score_modifier == 50
    assert any("failed" in finding.lower() for finding in result.findings)


@pytest.mark.asyncio
async def test_api_failure_returns_graceful_warning_fallback():
    repo = "devDays/PRism"
    get_routes = {
        f"https://api.github.com/repos/{repo}/pulls/4/files": MockResponse(500, {"message": "failure"}),
    }

    with patch("agents.coverage_agent.httpx.AsyncClient", return_value=MockAsyncClient(get_routes)):
        result = await run(pr_number=4, repo=repo)

    assert result.status == "warning"
    assert result.risk_score_modifier == 50
    assert any("failed" in finding.lower() for finding in result.findings)