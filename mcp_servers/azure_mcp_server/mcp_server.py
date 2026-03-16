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

    def __init__(self, recreate_index: bool = False, index_name: str | None = None):
        """Initialize index state and cache a SearchClient for follow-up queries."""
        from mcp_servers.azure_mcp_server.setup import create_index

        self._index_name = index_name
        create_index(recreate=recreate_index, index_name=index_name)

        # Expose a raw SearchClient for advanced usage (e.g. test scripts)
        try:
            from mcp_servers.azure_mcp_server.query import _get_search_client

            self.search_client = _get_search_client(index_name=index_name)
        except Exception as exc:
            logger.warning("Could not create raw search client: %s", exc)
            self.search_client = None

    # ── Query helpers used by the History Agent ───────────────────────

    def _query_with_client(
        self,
        search_text: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Run a search using the stored SearchClient.

        Uses ``self.search_client`` (created at init time with the correct
        credentials) so that per-repo Azure credentials are honoured even
        after ``HistoryAgent.__init__`` restores the original env vars.

        Falls back to the module-level helper only when no stored client
        is available (shouldn't happen in normal flow).
        """
        from mcp_servers.azure_mcp_server.query import _SELECT_FIELDS, _doc_to_incident

        client = self.search_client
        if client is None:
            # Last-resort fallback – re-reads env vars (may be wrong for
            # per-repo setups, but better than crashing).
            from mcp_servers.azure_mcp_server.query import query_semantic
            return query_semantic(search_text, top_k=top_k, index_name=self._index_name)

        try:
            results = client.search(
                search_text=search_text,
                top=top_k,
                select=_SELECT_FIELDS,
            )
            incidents = [_doc_to_incident(doc) for doc in results]
            logger.info("Query '%s' returned %d results", search_text, len(incidents))
            return incidents
        except Exception as exc:
            logger.error("AI Search query failed: %s", exc)
            return []

    def query_incidents_by_files_search(
        self,
        file_paths: list[str],
        top_k: int = 25,
    ) -> list[dict[str, Any]]:
        """Search incidents by file paths using the stored SearchClient."""
        if not file_paths:
            return []
        query_text = " OR ".join(file_paths)
        return self._query_with_client(query_text, top_k=top_k)

    def query_incidents_semantic(
        self,
        search_text: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text semantic search using the stored SearchClient."""
        return self._query_with_client(search_text, top_k=top_k)

    # ── Ingest helpers ────────────────────────────────────────────────

    def ingest_sample_data(self) -> None:
        """Upload hardcoded sample incidents (delegates to sample_data)."""
        from mcp_servers.azure_mcp_server.sample_data import upload_sample_data

        upload_sample_data()
