"""
Azure MCP Server for PRism History Agent
- Queries Azure Monitor Logs (Log Analytics) for incidents (optional; requires workspace access + table)
- Uses Azure AI Search for semantic incident retrieval (recommended for demo)
- Auto-creates the Azure AI Search index `incidents` if it does not exist
- Optional one-time sample data ingestion for quick demo bootstrapping

Run:
  py mcp_server.py
  py mcp_server.py --ingest-sample-data
"""
import os
import json
import sys
from typing import List, Dict, Any
from datetime import timedelta
from dotenv import load_dotenv

from azure.identity import ClientSecretCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchFieldDataType,
)

load_dotenv()


class AzureMCPServer:
    """
    MCP server that queries Azure Monitor Logs and Azure AI Search for incident data.
    """

    def __init__(self, search_index_name: str = "incidents"):
        self.subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        self.tenant_id = os.getenv("AZURE_TENANT_ID")
        self.client_id = os.getenv("AZURE_CLIENT_ID")
        self.client_secret = os.getenv("AZURE_CLIENT_SECRET")

        self.workspace_id = os.getenv("AZURE_LOG_WORKSPACE_ID")

        self.search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.search_key = os.getenv("AZURE_SEARCH_KEY")
        self.search_index_name = search_index_name

        self._validate_credentials()

        # Azure AD credential for Monitor/Logs
        self.credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        self.logs_client = LogsQueryClient(self.credential)

        # Azure AI Search clients
        self.search_credential = AzureKeyCredential(self.search_key)
        self.index_client = SearchIndexClient(
            endpoint=self.search_endpoint,
            credential=self.search_credential,
        )
        self._ensure_incidents_index()

        self.search_client = SearchClient(
            endpoint=self.search_endpoint,
            index_name=self.search_index_name,
            credential=self.search_credential,
        )

        print("[AzureMCPServer] ✅ Initialized successfully", flush=True)

    def _validate_credentials(self) -> None:
        required = [
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
            "AZURE_SEARCH_ENDPOINT",
            "AZURE_SEARCH_KEY",
        ]
        # Logs are optional for demo; only validate workspace if present
        # (you can still run semantic search without Log Analytics)
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise EnvironmentError(
                "[AzureMCPServer] ❌ Missing required env vars: "
                + ", ".join(missing)
                + "\nCreate a .env file with these variables."
            )

    def _ensure_incidents_index(self) -> None:
        """
        Create the Azure AI Search index if it doesn't exist.
        This fixes: "The index 'incidents' was not found."
        """
        index_name = self.search_index_name
        try:
            self.index_client.get_index(index_name)
            print(f"[AzureMCPServer] ✅ Search index '{index_name}' exists", flush=True)
            return
        except Exception:
            pass

        print(f"[AzureMCPServer] ℹ️ Creating search index '{index_name}'...", flush=True)

        index = SearchIndex(
            name=index_name,
            fields=[
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
                SimpleField(
                    name="files_involved",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    filterable=True,
                    facetable=True,
                ),
                SimpleField(
                    name="affected_services",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    filterable=True,
                    facetable=True,
                ),
                SimpleField(
                    name="duration_minutes",
                    type=SearchFieldDataType.Int32,
                    filterable=True,
                    sortable=True,
                ),
            ],
        )

        self.index_client.create_index(index)
        print(f"[AzureMCPServer] ✅ Created search index '{index_name}'", flush=True)

    # -----------------------------
    # Azure Monitor Logs (Optional)
    # -----------------------------
    def query_incidents_by_files_from_log_analytics(
        self, file_paths: List[str], days_back: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Optional: Query Log Analytics for incidents involving specific files.

        NOTE:
        - Requires AZURE_LOG_WORKSPACE_ID set
        - Requires IAM role on the workspace (Log Analytics Reader / Monitoring Reader)
        - Requires the table you're querying to actually exist

        If you don't have a real table yet, use Azure AI Search methods below for demo.
        """
        if not self.workspace_id:
            print(
                "[AzureMCPServer] ⚠️ AZURE_LOG_WORKSPACE_ID not set. Skipping Log Analytics query.",
                flush=True,
            )
            return []

        # This table name is just an example. You MUST change it to a real table you have.
        # If you don't have one yet, this will always fail.
        table_name = os.getenv("AZURE_LOG_INCIDENT_TABLE", "CustomTable_Incidents_CL")

        file_list = ", ".join([f'"{f}"' for f in file_paths])
        kql_query = f"""
        {table_name}
        | where TimeGenerated >= ago({days_back}d)
        | where tostring(files_involved_s) contains_any ({file_list})
        | project
            IncidentID=tostring(id_s),
            Timestamp=TimeGenerated,
            Title=tostring(title_s),
            Severity=tostring(severity_s),
            FilesInvolved=tostring(files_involved_s),
            ErrorMessage=tostring(error_message_s),
            RootCause=tostring(root_cause_s),
            AffectedServices=tostring(affected_services_s),
            DurationMinutes=toint(duration_minutes_d)
        | sort by Timestamp desc
        """

        try:
            print(
                f"[AzureMCPServer] 🔍 Querying Log Analytics incidents for files: {file_paths}",
                flush=True,
            )
            result = self.logs_client.query_workspace(
                workspace_id=self.workspace_id,
                query=kql_query,
                timespan=timedelta(days=days_back),
            )

            incidents: List[Dict[str, Any]] = []
            if result.status == LogsQueryStatus.SUCCESS:
                if not result.tables:
                    return []
                table = result.tables[0]
                for row in table.rows:
                    incidents.append(
                        {
                            "id": row[0],
                            "timestamp": str(row[1]),
                            "title": row[2],
                            "severity": row[3],
                            "files_involved": [x.strip() for x in (row[4] or "").split(",") if x.strip()],
                            "error_message": row[5],
                            "root_cause": row[6],
                            "affected_services": [x.strip() for x in (row[7] or "").split(",") if x.strip()],
                            "duration_minutes": int(row[8] or 0),
                            "source": "log_analytics",
                        }
                    )
                print(f"[AzureMCPServer] ✅ Found {len(incidents)} incidents", flush=True)
                return incidents

            if result.status == LogsQueryStatus.PARTIAL:
                print(f"[AzureMCPServer] ⚠️ Partial results: {result.error}", flush=True)
                return []

            print(f"[AzureMCPServer] ❌ Query failed: {result.error}", flush=True)
            return []

        except Exception as e:
            print(f"[AzureMCPServer] ❌ Error querying logs: {e}", flush=True)
            return []

    # -----------------------------
    # Azure AI Search (Recommended)
    # -----------------------------
    def query_incidents_semantic(self, query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Semantic-ish search using Azure AI Search full-text search.
        (True vector/semantic configs are optional; this works immediately once index has docs.)
        """
        try:
            print(f"[AzureMCPServer] 🔍 Search: '{query_text}'", flush=True)

            results = self.search_client.search(
                search_text=query_text,
                top=top_k,
                select=[
                    "id",
                    "title",
                    "severity",
                    "files_involved",
                    "timestamp",
                    "root_cause",
                    "error_message",
                    "affected_services",
                    "duration_minutes",
                ],
            )

            incidents: List[Dict[str, Any]] = []
            for doc in results:
                incidents.append(
                    {
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
                        "source": "azure_ai_search",
                    }
                )

            print(f"[AzureMCPServer] ✅ Found {len(incidents)} results", flush=True)
            return incidents

        except Exception as e:
            print(f"[AzureMCPServer] ❌ Search failed: {e}", flush=True)
            return []

    def query_incidents_by_files_search(self, file_paths: List[str], top_k: int = 25) -> List[Dict[str, Any]]:
        """
        Convenience method: query Azure AI Search by file paths.
        """
        if not file_paths:
            return []
        query_text = " OR ".join(file_paths)
        return self.query_incidents_semantic(query_text=query_text, top_k=top_k)

    def ingest_sample_data(self) -> None:
        """
        Upload sample incident data to Azure AI Search.
        Run once (or any time to refresh). Documents are keyed by 'id', so re-upload updates.
        """
        sample_incidents = [
            {
                "id": "INC-2026-0001",
                "timestamp": "2026-02-24T14:30:00Z",
                "title": "Payment service timeout spike",
                "severity": "high",
                "files_involved": ["payment_service.py"],
                "error_message": "ConnectionPoolError: max retries exceeded",
                "root_cause": "Retry handler removed in PR #892",
                "affected_services": ["payments-api", "checkout-service"],
                "duration_minutes": 45,
            },
            {
                "id": "INC-2026-0002",
                "timestamp": "2026-02-22T09:15:00Z",
                "title": "Database migration deadlock",
                "severity": "critical",
                "files_involved": ["database.py", "models/user.py"],
                "error_message": "Deadlock detected between migration and active queries",
                "root_cause": "Schema change without proper locking",
                "affected_services": ["auth-service", "user-api"],
                "duration_minutes": 120,
            },
            {
                "id": "INC-2026-0003",
                "timestamp": "2026-02-20T16:45:00Z",
                "title": "Memory leak in payment retry loop",
                "severity": "high",
                "files_involved": ["payment_service.py"],
                "error_message": "Memory usage climbed from 512MB to 2.8GB",
                "root_cause": "Retry handler not releasing connections",
                "affected_services": ["payments-api"],
                "duration_minutes": 90,
            },
            {
                "id": "INC-2026-0004",
                "timestamp": "2026-02-18T11:20:00Z",
                "title": "Payment processing failures during peak traffic",
                "severity": "critical",
                "files_involved": ["payment_service.py", "retry_handler.py"],
                "error_message": "HTTP 503 on 40% of payment endpoints",
                "root_cause": "Insufficient retry logic",
                "affected_services": ["payments-api", "checkout-service"],
                "duration_minutes": 65,
            },
            {
                "id": "INC-2026-0005",
                "timestamp": "2026-02-15T13:00:00Z",
                "title": "Authentication bypass via hardcoded token",
                "severity": "critical",
                "files_involved": ["auth_service.py"],
                "error_message": "Hardcoded API key exposed in logs",
                "root_cause": "Debug token left in production",
                "affected_services": ["auth-service", "api-gateway"],
                "duration_minutes": 180,
            },
        ]

        try:
            print(
                f"[AzureMCPServer] 📤 Uploading {len(sample_incidents)} incidents to index '{self.search_index_name}'...",
                flush=True,
            )
            result = self.search_client.upload_documents(documents=sample_incidents)
            # result is a list of IndexingResult
            succeeded = sum(1 for r in result if r.succeeded)
            failed = [r for r in result if not r.succeeded]
            print(f"[AzureMCPServer] ✅ Upload complete. Succeeded={succeeded}, Failed={len(failed)}", flush=True)
            if failed:
                print("[AzureMCPServer] ⚠️ Failed docs:", flush=True)
                for f in failed:
                    print(f"  - key={f.key} error={f.error_message}", flush=True)
        except Exception as e:
            print(f"[AzureMCPServer] ❌ Failed to ingest data: {e}", flush=True)


def main():
    ingest = "--ingest-sample-data" in sys.argv
    mcp = AzureMCPServer()

    if ingest:
        mcp.ingest_sample_data()

    print("\n--- Test 1: Log Analytics Query by Files (optional) ---")
    incidents = mcp.query_incidents_by_files_from_log_analytics(["payment_service.py"])
    print(json.dumps(incidents, indent=2, default=str))

    print("\n--- Test 2: Azure AI Search query by Files ---")
    incidents = mcp.query_incidents_by_files_search(["payment_service.py"])
    print(json.dumps(incidents, indent=2, default=str))

    print("\n--- Test 3: Azure AI Search semantic query ---")
    incidents = mcp.query_incidents_semantic("retry logic failures")
    print(json.dumps(incidents, indent=2, default=str))


if __name__ == "__main__":
    main()