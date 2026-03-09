"""
PRism Azure AI Search — Incident Query
========================================
Read-only module used by the History Agent at PR analysis time.
Queries Azure AI Search for incidents matching changed file paths.

This file is independent of setup.py and ingest.py.
It only READS from AI Search — never writes.
"""

import os
import logging
from typing import Any

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

load_dotenv()
logger = logging.getLogger("prism.query")

INDEX_NAME = "incidents"

# Fields returned from every query — matches the index schema
_SELECT_FIELDS = [
    "id",
    "title",
    "severity",
    "files_involved",
    "timestamp",
    "root_cause",
    "error_message",
    "affected_services",
    "duration_minutes",
]


def _get_search_client() -> SearchClient:
    """Create an authenticated SearchClient for the incidents index."""
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    key = os.getenv("AZURE_SEARCH_KEY")

    if not endpoint:
        raise EnvironmentError("AZURE_SEARCH_ENDPOINT is required")

    credential: Any
    if key:
        credential = AzureKeyCredential(key)
    else:
        credential = DefaultAzureCredential()

    return SearchClient(
        endpoint=endpoint,
        index_name=INDEX_NAME,
        credential=credential,
    )


def _doc_to_incident(doc: dict) -> dict[str, Any]:
    """Normalize an AI Search document to the standard incident dict."""
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "severity": doc.get("severity"),
        "files_involved": doc.get("files_involved", []),
        "timestamp": doc.get("timestamp"),
        "root_cause": doc.get("root_cause"),
        "error_message": doc.get("error_message"),
        "affected_services": doc.get("affected_services", []),
        "duration_minutes": doc.get("duration_minutes", 0),
        "score": doc.get("@search.score"),
    }


def query_by_files(file_paths: list[str], top_k: int = 25) -> list[dict[str, Any]]:
    """
    Search incidents by file paths. Primary method used by the History Agent.

    Args:
        file_paths: List of changed files from the PR.
        top_k: Max results to return.

    Returns:
        List of incident dicts matching the standard schema.
    """
    if not file_paths:
        return []

    query_text = " OR ".join(file_paths)
    return query_semantic(query_text, top_k=top_k)


def query_semantic(query_text: str, top_k: int = 10) -> list[dict[str, Any]]:
    """
    Full-text search across the incidents index.

    Searches across: files_involved, title, error_message, root_cause.
    """
    try:
        client = _get_search_client()

        results = client.search(
            search_text=query_text,
            top=top_k,
            select=_SELECT_FIELDS,
        )

        incidents = [_doc_to_incident(doc) for doc in results]
        logger.info("Query '%s' returned %d results", query_text, len(incidents))
        return incidents

    except Exception as exc:
        logger.error("AI Search query failed: %s", exc)
        return []