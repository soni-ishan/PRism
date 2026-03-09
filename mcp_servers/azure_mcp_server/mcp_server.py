"""
PRism Azure MCP Server — Facade Class
======================================
Wraps the query, setup, and sample_data modules into a single
``AzureMCPServer`` class used by the History Agent and test scripts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("prism.azure_mcp_server")


class AzureMCPServer:
    """Thin wrapper around PRism's Azure AI Search query/ingest modules."""

    def __init__(self, recreate_index: bool = False):
        from mcp_servers.azure_mcp_server.setup import create_index

        create_index(recreate=recreate_index)

        # Expose a raw SearchClient for advanced usage (e.g. test scripts)
        try:
            from mcp_servers.azure_mcp_server.query import _get_search_client

            self.search_client = _get_search_client()
        except Exception as exc:
            logger.warning("Could not create raw search client: %s", exc)
            self.search_client = None

    # ── Query helpers used by the History Agent ───────────────────────

    def query_incidents_by_files_search(
        self,
        file_paths: list[str],
        top_k: int = 25,
    ) -> list[dict[str, Any]]:
        """Search incidents by file paths (delegates to query.query_by_files)."""
        from mcp_servers.azure_mcp_server.query import query_by_files

        return query_by_files(file_paths, top_k=top_k)

    def query_incidents_semantic(
        self,
        search_text: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text semantic search (delegates to query.query_semantic)."""
        from mcp_servers.azure_mcp_server.query import query_semantic

        return query_semantic(search_text, top_k=top_k)

    # ── Ingest helpers ────────────────────────────────────────────────

    def ingest_sample_data(self) -> None:
        """Upload hardcoded sample incidents (delegates to sample_data)."""
        from mcp_servers.azure_mcp_server.sample_data import upload_sample_data

        upload_sample_data()
