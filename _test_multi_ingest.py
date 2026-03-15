"""Test multi-repo ingestion pipeline."""
import asyncio, os, sys, dotenv, logging
dotenv.load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from mcp_servers.azure_mcp_server.ingest import ingest_all_repos, fetch_all_registrations
import json

async def test():
    # 1. Show what registrations we'll iterate over
    regs = await fetch_all_registrations()
    print(f"\n=== {len(regs)} active registrations with Azure workspace ===")
    for r in regs:
        print(f"  {r['owner']}/{r['repo']} -> workspace={r['azure_customer_id'][:8]}... index={r['index_name']}")

    # 2. Run ingest for all repos
    print("\n=== Running ingest_all_repos ===")
    summary = await ingest_all_repos(
        fired_time="2026-03-15T03:00:00Z",
        window_minutes=1440,
    )
    print(f"\n=== RESULTS ===")
    print(json.dumps(summary, indent=2, default=str))

asyncio.run(test())
