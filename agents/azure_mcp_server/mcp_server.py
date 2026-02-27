"""
Azure MCP Server for PRism History Agent
Connects to Azure Monitor Logs and Azure AI Search
"""
import os
import json
from typing import List, Dict, Any
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Azure SDK imports
from azure.identity import ClientSecretCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

load_dotenv()


class AzureMCPServer:
    """
    MCP Server that queries Azure Monitor Logs and Azure AI Search
    for incident data related to specific files.
    """
    
    def __init__(self):
        """Initialize Azure clients with credentials from .env"""
        self.subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        self.tenant_id = os.getenv("AZURE_TENANT_ID")
        self.client_id = os.getenv("AZURE_CLIENT_ID")
        self.client_secret = os.getenv("AZURE_CLIENT_SECRET")
        self.workspace_id = os.getenv("AZURE_LOG_WORKSPACE_ID")
        self.search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.search_key = os.getenv("AZURE_SEARCH_KEY")
        
        # Validate credentials
        self._validate_credentials()
        
        # Initialize Azure clients
        self.credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        
        self.logs_client = LogsQueryClient(self.credential)
        
        self.search_client = SearchClient(
            endpoint=self.search_endpoint,
            index_name="incidents",
            credential=AzureKeyCredential(self.search_key)
        )
        
        print("[AzureMCPServer] ✅ Initialized successfully", flush=True)
    
    def _validate_credentials(self) -> None:
        """Check if all required credentials are present."""
        required = [
            "AZURE_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID", 
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
            "AZURE_LOG_WORKSPACE_ID",
            "AZURE_SEARCH_ENDPOINT",
            "AZURE_SEARCH_KEY"
        ]
        
        missing = [var for var in required if not os.getenv(var)]
        
        if missing:
            raise EnvironmentError(
                f"[AzureMCPServer] ❌ Missing credentials: {', '.join(missing)}\n"
                f"Create a .env file with these variables."
            )
    
    def query_incidents_by_files(self, file_paths: List[str], days_back: int = 30) -> List[Dict[str, Any]]:
        """
        Query Azure Monitor Logs for incidents involving specific files.
        
        Args:
            file_paths: List of files changed in PR (e.g., ["payment_service.py"])
            days_back: How many days of logs to search (default 30)
        
        Returns:
            List of incidents with files_involved, timestamp, severity, etc.
        """
        
        incidents = []
        
        # Build KQL (Kusto Query Language) query
        file_list = ", ".join([f'"{f}"' for f in file_paths])
        
        kql_query = f"""
        CustomTable_Incidents_CL
        | where files_involved_s contains_any ({file_list})
        | where TimeGenerated >= ago({days_back}d)
        | project
            IncidentID=id_s,
            Timestamp=TimeGenerated,
            Title=title_s,
            Severity=severity_s,
            FilesInvolved=files_involved_s,
            ErrorMessage=error_message_s,
            RootCause=root_cause_s,
            AffectedServices=affected_services_s,
            DurationMinutes=duration_minutes_d
        | sort by Timestamp desc
        """
        
        try:
            print(f"[AzureMCPServer] 🔍 Querying incidents for files: {file_paths}", flush=True)
            
            result = self.logs_client.query_workspace(
                workspace_id=self.workspace_id,
                query=kql_query,
                timespan=timedelta(days=days_back)
            )
            
            if result.status == LogsQueryStatus.SUCCESS:
                for row in result.tables[0].rows:
                    incident = {
                        "id": row[0],
                        "timestamp": str(row[1]),
                        "title": row[2],
                        "severity": row[3],
                        "files_involved": row[4].split(",") if row[4] else [],
                        "error_message": row[5],
                        "root_cause": row[6],
                        "affected_services": row[7].split(",") if row[7] else [],
                        "duration_minutes": int(row[8]) if row[8] else 0
                    }
                    incidents.append(incident)
                
                print(f"[AzureMCPServer] ✅ Found {len(incidents)} incidents", flush=True)
            
            elif result.status == LogsQueryStatus.PARTIAL:
                print(f"[AzureMCPServer] ⚠️  Partial results: {result.error}", flush=True)
            else:
                print(f"[AzureMCPServer] ❌ Query failed: {result.error}", flush=True)
        
        except Exception as e:
            print(f"[AzureMCPServer] ❌ Error querying logs: {e}", flush=True)
            # Fallback: return empty list
            incidents = []
        
        return incidents
    
    def query_incidents_semantic(self, query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Semantic search for incidents using Azure AI Search.
        
        Example: "What incidents involved retry logic failures?"
        
        Args:
            query_text: Natural language query
            top_k: Number of results to return
        
        Returns:
            List of semantically relevant incidents
        """
        
        try:
            print(f"[AzureMCPServer] 🔍 Semantic search: '{query_text}'", flush=True)
            
            results = self.search_client.search(
                search_text=query_text,
                top=top_k,
                select=["id", "title", "severity", "files_involved", "timestamp", "root_cause"]
            )
            
            incidents = []
            for doc in results:
                incidents.append({
                    "id": doc.get("id"),
                    "title": doc.get("title"),
                    "severity": doc.get("severity"),
                    "files_involved": doc.get("files_involved", []),
                    "timestamp": doc.get("timestamp"),
                    "root_cause": doc.get("root_cause"),
                    "score": doc.get("@search.score")  # Relevance score
                })
            
            print(f"[AzureMCPServer] ✅ Found {len(incidents)} relevant incidents", flush=True)
            return incidents
        
        except Exception as e:
            print(f"[AzureMCPServer] ❌ Semantic search failed: {e}", flush=True)
            return []
    
    def ingest_sample_data(self) -> None:
        """
        Upload sample incident data to Azure AI Search.
        Run this once to populate the index with test data.
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
                "duration_minutes": 45
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
                "duration_minutes": 120
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
                "duration_minutes": 90
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
                "duration_minutes": 65
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
                "duration_minutes": 180
            }
        ]
        
        try:
            print(f"[AzureMCPServer] 📤 Uploading {len(sample_incidents)} incidents to Azure AI Search...", flush=True)
            
            result = self.search_client.upload_documents(sample_incidents)
            
            print(f"[AzureMCPServer] ✅ Uploaded {len(result)} documents successfully", flush=True)
        
        except Exception as e:
            print(f"[AzureMCPServer] ❌ Failed to ingest data: {e}", flush=True)


def main():
    """Test the MCP server."""
    try:
        mcp = AzureMCPServer()
        
        # Test 1: Query by files
        print("\n--- Test 1: Query by Files ---")
        incidents = mcp.query_incidents_by_files(["payment_service.py"])
        print(json.dumps(incidents, indent=2, default=str))
        
        # Test 2: Semantic search
        print("\n--- Test 2: Semantic Search ---")
        incidents = mcp.query_incidents_semantic("retry logic failures")
        print(json.dumps(incidents, indent=2, default=str))
    
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()