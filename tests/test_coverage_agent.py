import pytest
import respx
import httpx
from agents.coverage_agent import run

@pytest.mark.asyncio
@respx.mock
async def test_coverage_pass():
    # Mock the PR files list
    respx.get("https://api.github.com/repos/devDays/PRism/pulls/1/files").respond(
        json=[{"filename": "agents/logic.py", "status": "modified"}]
    )
    # Mock the test file check (Status 200 means it exists)
    respx.get("https://api.github.com/repos/devDays/PRism/contents/tests/test_logic.py").respond(status_code=200)

    result = await run(pr_number=1, repo="devDays/PRism")
    assert result.status == "pass"
    assert result.risk_score_modifier == 0

@pytest.mark.asyncio
@respx.mock
async def test_coverage_warning():
    respx.get("https://api.github.com/repos/devDays/PRism/pulls/1/files").respond(
        json=[{"filename": "agents/new_feature.py", "status": "added"}]
    )
    # Status 404 means test file is missing
    respx.get("https://api.github.com/repos/devDays/PRism/contents/tests/test_new_feature.py").respond(status_code=404)
    respx.post("https://api.github.com/repos/devDays/PRism/issues").respond(status_code=201)

    result = await run(pr_number=1, repo="devDays/PRism")
    assert result.status == "warning"
    assert result.risk_score_modifier == 15