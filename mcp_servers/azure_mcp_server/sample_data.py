"""
PRism — Sample Incident Data
=============================
Dev/demo utility: uploads hardcoded test incidents to AI Search.
NOT used in production — real incidents come from ingest.py.

Usage:
    python -m mcp_servers.azure_mcp_server.sample_data
"""

import sys
import logging

from mcp_servers.azure_mcp_server.setup import create_index
from mcp_servers.azure_mcp_server.ingest import push_incident

logger = logging.getLogger("prism.sample_data")

SAMPLE_INCIDENTS = [
    {
        "id": "INC-2026-0001",
        "timestamp": "2026-02-24T14:30:00Z",
        "title": "Payment service timeout spike",
        "severity": "high",
        "files_involved": ["src/services/payment_service.py"],
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
        "files_involved": ["src/db/database.py", "src/models/user.py"],
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
        "files_involved": ["src/services/payment_service.py"],
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
        "files_involved": ["src/services/payment_service.py", "src/utils/retry_handler.py"],
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
        "files_involved": ["src/auth/auth_service.py"],
        "error_message": "Hardcoded API key exposed in logs",
        "root_cause": "Debug token left in production",
        "affected_services": ["auth-service", "api-gateway"],
        "duration_minutes": 180,
    },
    {
        "id": "INC-2026-0006",
        "timestamp": "2026-02-12T18:30:00Z",
        "title": "Payment gateway connection timeout",
        "severity": "high",
        "files_involved": ["src/api/payments.py", "src/clients/gateway_client.py"],
        "error_message": "Timeout connecting to payment gateway after 30s",
        "root_cause": "Payment gateway client timeout too aggressive",
        "affected_services": ["payments-api"],
        "duration_minutes": 75,
    },
]


def upload_sample_data() -> None:
    """Ensure index exists, then upload all sample incidents."""
    create_index(recreate=False)

    succeeded = 0
    for incident in SAMPLE_INCIDENTS:
        if push_incident(incident):
            succeeded += 1

    print(f"✅ Uploaded {succeeded}/{len(SAMPLE_INCIDENTS)} sample incidents")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 60)
    print("PRism — Upload Sample Incident Data")
    print("=" * 60)

    try:
        upload_sample_data()
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()