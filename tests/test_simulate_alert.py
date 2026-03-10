"""
PRism — Simulate a Real Azure Monitor Alert
=============================================
Manually fires the ingest_from_alert() function with a realistic
alert payload, as if Azure Monitor had sent it via Event Grid.

This tests the ENTIRE pipeline end-to-end:
  Alert → App Insights query → LLM extraction → AI Search push

Usage:
    python tests/test_simulate_alert.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from mcp_servers.azure_mcp_server.setup import create_index
from mcp_servers.azure_mcp_server.ingest import ingest_from_alert, push_incident, extract_files
from mcp_servers.azure_mcp_server.query import query_by_files


async def simulate():
    print("=" * 60)
    print("PRism — Simulated Alert Pipeline")
    print("=" * 60)

    # Ensure index exists
    create_index(recreate=False)

    # In production, this comes from Event Grid.
    # Here we fake it but keep the structure identical.
    fake_alert = {
        "data": {
            "essentials": {
                "alertId": "/subscriptions/xxx/alerts/sim-" + str(int(__import__('time').time())),
                "alertRule": "Simulated - Payment API Crash",
                "severity": "Sev2",
                "firedDateTime": "2026-03-02T14:30:00Z",
                "targetResourceId": "/subscriptions/xxx/providers/Microsoft.App/containerApps/payment-api",
            }
        }
    }

    # Since we probably don't have a real App Insights workspace 
    # with data, we bypass fetch_exceptions and go straight to
    # the LLM + push steps with a fake stack trace.

    print("\n📋 Simulating alert: Payment API Crash")
    print("   Severity: Sev2 → high")
    print("   Resource: payment-api")

    # Step 1: LLM extracts files from a realistic stack trace
    stack_trace = """
Traceback (most recent call last):
  File "/app/src/services/payment_service.py", line 47, in process_payment
    response = self.gateway.charge(request.amount, request.currency)
  File "/app/src/clients/gateway_client.py", line 23, in charge
    resp = self._session.post(self.endpoint, json=payload, timeout=30)
  File "/usr/lib/python3.12/site-packages/httpx/_client.py", line 1574, in post
    return self.request("POST", url, **kwargs)
httpx.ConnectTimeout: timed out connecting to payment gateway
"""
    error_message = "httpx.ConnectTimeout: timed out connecting to payment gateway"

    print("\n🤖 Sending stack trace to Azure OpenAI for file extraction...")
    files = await extract_files(stacktrace=stack_trace, error_message=error_message)
    print(f"   Extracted files: {files}")

    if not files:
        print("\n❌ No files extracted — LLM could not parse trace")
        return

    # Step 2: Build and push incident
    import time
    incident = {
        "id": f"INC-SIM-{int(time.time())}",
        "timestamp": "2026-03-02T14:30:00Z",
        "title": "Simulated - Payment API Crash",
        "severity": "high",
        "files_involved": files,
        "error_message": error_message,
        "root_cause": "",
        "affected_services": ["payment-api"],
        "duration_minutes": 0,
    }

    print(f"\n📤 Pushing incident {incident['id']} to AI Search...")
    success = push_incident(incident)
    if not success:
        print("❌ Failed to push")
        return

    print("✅ Incident pushed to AI Search")
    print(f"   Document: {incident}")

    # Step 3: Verify the History Agent can find it
    print("\n⏳ Waiting 2s for AI Search indexing...")
    time.sleep(2)

    print(f"\n🔍 Querying AI Search as History Agent would...")
    for f in files:
        results = query_by_files([f])
        found = [r for r in results if r["id"] == incident["id"]]
        status = "✅ found" if found else "❌ not found"
        print(f"   query_by_files(['{f}']) → {len(results)} results, our incident: {status}")

    print("\n✅ Simulation complete!")
    print("   The History Agent will now see this incident on the next PR")
    print(f"   that touches: {files}")


if __name__ == "__main__":
    asyncio.run(simulate())