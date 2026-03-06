"""
PRism Azure AI Search — Index Setup
====================================
One-time setup: creates the "incidents" index in Azure AI Search
with the correct schema. Run during deployment or manually.

Usage:
    python -m mcp_servers.azure_mcp_server.setup
    python -m mcp_servers.azure_mcp_server.setup --recreate
"""

import os
import sys
import logging

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchField,
    SearchFieldDataType,
)

load_dotenv()
logger = logging.getLogger("prism.setup")


# ── Index Schema ─────────────────────────────────────────────
# Single source of truth for the incidents index schema.
# Both setup.py and ingest.py reference this structure.

INDEX_NAME = "incidents"

INDEX_FIELDS = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True),
    SearchableField(name="title", type=SearchFieldDataType.String),
    SimpleField(
        name="severity",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),
    SimpleField(
        name="timestamp",
        type=SearchFieldDataType.String,
        filterable=True,
        sortable=True,
    ),
    SearchableField(name="root_cause", type=SearchFieldDataType.String),
    SearchableField(name="error_message", type=SearchFieldDataType.String),
    SearchField(
        name="files_involved",
        type=SearchFieldDataType.Collection(SearchFieldDataType.String),
        searchable=True,
        filterable=True,
        facetable=True,
    ),
    SearchField(
        name="affected_services",
        type=SearchFieldDataType.Collection(SearchFieldDataType.String),
        searchable=True,
        filterable=True,
        facetable=True,
    ),
    SimpleField(
        name="duration_minutes",
        type=SearchFieldDataType.Int32,
        filterable=True,
        sortable=True,
    ),
]


def _get_index_client() -> SearchIndexClient:
    """Create an authenticated SearchIndexClient."""
    endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT") or os.getenv("AZURE_SEARCH_ENDPOINT")
    key = os.getenv("AZURE_AI_SEARCH_KEY") or os.getenv("AZURE_SEARCH_KEY")

    if not endpoint:
        raise EnvironmentError("AZURE_AI_SEARCH_ENDPOINT is required")

    if key:
        return SearchIndexClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )

    # Fall back to Managed Identity (production)
    return SearchIndexClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )


def create_index(recreate: bool = False) -> None:
    """Create the incidents index. If recreate=True, drops and recreates it."""
    client = _get_index_client()

    if recreate:
        try:
            client.delete_index(INDEX_NAME)
            logger.info("Deleted existing index '%s'", INDEX_NAME)
        except Exception:
            pass  # Index didn't exist — that's fine
    else:
        try:
            client.get_index(INDEX_NAME)
            logger.info("Index '%s' already exists — skipping", INDEX_NAME)
            return
        except Exception:
            pass  # Index doesn't exist — create it

    index = SearchIndex(name=INDEX_NAME, fields=INDEX_FIELDS)
    client.create_index(index)
    logger.info("Created index '%s'", INDEX_NAME)


def validate_credentials() -> dict[str, bool]:
    """Check which Azure credentials are available. Returns a status dict."""
    checks = {
        "AZURE_SEARCH_ENDPOINT": bool(os.getenv("AZURE_SEARCH_ENDPOINT")),
        "AZURE_SEARCH_KEY": bool(os.getenv("AZURE_SEARCH_KEY")),
        "AZURE_OPENAI_ENDPOINT": bool(os.getenv("AZURE_OPENAI_ENDPOINT")),
        "AZURE_OPENAI_DEPLOYMENT": bool(os.getenv("AZURE_OPENAI_DEPLOYMENT")),
        "AZURE_LOG_WORKSPACE_ID": bool(os.getenv("AZURE_LOG_WORKSPACE_ID")),
    }
    return checks


# ── CLI ──────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    recreate = "--recreate" in sys.argv

    print("=" * 60)
    print("PRism — Azure AI Search Index Setup")
    print("=" * 60)

    # Check credentials
    creds = validate_credentials()
    for name, ok in creds.items():
        status = "✅" if ok else "❌ missing"
        print(f"  {name}: {status}")
    print()

    if not creds["AZURE_SEARCH_ENDPOINT"]:
        print("Cannot proceed without AZURE_SEARCH_ENDPOINT")
        sys.exit(1)

    # Create index
    try:
        create_index(recreate=recreate)
        print(f"\n✅ Index '{INDEX_NAME}' is ready")
    except Exception as e:
        print(f"\n❌ Setup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()