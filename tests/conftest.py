"""
PRism – Root Pytest Configuration
==================================
Defines shared markers, fixtures, and automatic skip logic so that:

  tests/unit/        — pure unit / mock tests (run everywhere, no credentials needed)
  tests/integration/ — end-to-end tests (run locally and in CI with full infra)

Markers
-------
  @pytest.mark.unit             All mock-based unit tests.  Auto-applied to tests/unit/.
  @pytest.mark.integration      Tests that exercise real agent logic end-to-end.
                                External Azure services are still stubbed unless the
                                ``azure_required`` flag is also present.
  @pytest.mark.azure_required   Tests that call live Azure services (Search, OpenAI,
                                Content Safety).  Skipped automatically when the
                                minimum env-var set is absent.
  @pytest.mark.foundry_required Tests that make real Azure OpenAI calls AND emit
                                actual OpenTelemetry spans to Application Insights
                                so they appear in the Foundry Tracing dashboard.
                                Skipped unless all four Foundry env vars are set.

Environment variables checked for azure_required
-------------------------------------------------
  AZURE_SEARCH_ENDPOINT, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY

Environment variables checked for foundry_required
--------------------------------------------------
  APPLICATIONINSIGHTS_CONNECTION_STRING
  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_DEPLOYMENT
"""

from __future__ import annotations

import os

import pytest


# ── Marker registration ──────────────────────────────────────────────


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: pure mock/unit test – no real I/O")
    config.addinivalue_line(
        "markers",
        "integration: end-to-end test – real agent logic, external Azure calls are stubbed",
    )
    config.addinivalue_line(
        "markers",
        "azure_required: requires live Azure credentials (skipped when env vars are absent)",
    )
    config.addinivalue_line(
        "markers",
        "foundry_required: makes real Azure OpenAI calls and emits live traces to "
        "Application Insights / Foundry (skipped when credentials are absent)",
    )


# ── Auto-apply markers based on test location ────────────────────────


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        path = str(item.fspath)
        if "tests/unit" in path.replace("\\", "/") or "tests\\unit" in path:
            item.add_marker(pytest.mark.unit)
        elif "tests/integration" in path.replace("\\", "/") or "tests\\integration" in path:
            item.add_marker(pytest.mark.integration)


# ── azure_required skip logic ────────────────────────────────────────

_AZURE_REQUIRED_VARS = (
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
)

_FOUNDRY_REQUIRED_VARS = (
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
)


def _azure_creds_available() -> bool:
    return all(os.getenv(v) for v in _AZURE_REQUIRED_VARS)


def _foundry_creds_available() -> bool:
    return all(os.getenv(v) for v in _FOUNDRY_REQUIRED_VARS)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("foundry_required") and not _foundry_creds_available():
        missing = [v for v in _FOUNDRY_REQUIRED_VARS if not os.getenv(v)]
        pytest.skip(
            f"foundry_required: missing env vars {missing!r}. "
            "Set them to emit real traces to Azure AI Foundry."
        )
    elif item.get_closest_marker("azure_required") and not _azure_creds_available():
        missing = [v for v in _AZURE_REQUIRED_VARS if not os.getenv(v)]
        pytest.skip(
            f"azure_required: missing env vars {missing!r}. "
            "Set them to run Azure-live integration tests."
        )
