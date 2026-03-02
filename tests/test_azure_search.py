"""
Setup Azure AI Search for PRism History Agent
=============================================
This script populates Azure AI Search with sample incident data for testing.

Usage:
    python test_azure_search.py

Requires:
    - Azure credentials configured in .env file
    - Azure AI Search service created
"""
import sys
import os

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from mcp_servers.azure_mcp_server.mcp_server import AzureMCPServer


def main():
    print("=" * 70)
    print("PRISM - Azure AI Search Setup")
    print("=" * 70)
    print()
    
    try:
        print("📡 Connecting to Azure AI Search...")
        print("🔄 Recreating index with updated schema...")
        mcp = AzureMCPServer(recreate_index=True)
        print("✅ Connected successfully")
        print()
        
        print("📤 Uploading sample incident data to 'incidents' index...")
        mcp.ingest_sample_data()
        print()
        
        print("✅ Setup complete!")
        print()
        print("🧪 Test the History Agent:")
        print("   python agents/history_agent/agent.py src/services/payment_service.py")
        print()
        
    except Exception as e:
        print(f"❌ Setup failed: {e}")
        print()
        print("📝 Troubleshooting:")
        print("  1. Ensure .env file exists with Azure credentials:")
        print("     - AZURE_TENANT_ID")
        print("     - AZURE_CLIENT_ID")
        print("     - AZURE_CLIENT_SECRET")
        print("     - AZURE_SEARCH_ENDPOINT")
        print("     - AZURE_SEARCH_KEY")
        print()
        print("  2. Verify your Azure Search service is running")
        print("  3. Check service principal has 'Search Service Contributor' role")
        print()
        sys.exit(1)

      # Test 1: Get all documents (empty search)
    print("Test 1: Get all documents in index")
    print("-" * 70)
    results = mcp.search_client.search(search_text="*", top=50)
    all_docs = list(results)
    print(f"Total documents in index: {len(all_docs)}")
    print()
    
    if all_docs:
        print("Sample document:")
        doc = all_docs[0]
        for key, value in doc.items():
            if not key.startswith("@"):
                print(f"  {key}: {value}")
        print()
    
    # Test 2: Search for specific file
    print("Test 2: Search for 'payment_service.py'")
    print("-" * 70)
    results = mcp.query_incidents_semantic("payment_service.py", top_k=10)
    print(f"Results: {len(results)}")
    for r in results:
        print(f"  - {r['title']} | files: {r['files_involved']}")
    print()
    
    # Test 3: Search with full path
    print("Test 3: Search for 'src/services/payment_service.py'")
    print("-" * 70)
    results = mcp.query_incidents_semantic("src/services/payment_service.py", top_k=10)
    print(f"Results: {len(results)}")
    for r in results:
        print(f"  - {r['title']} | files: {r['files_involved']}")
    print()
    
    # Test 4: Search with OR
    print("Test 4: Search with OR operator")
    print("-" * 70)
    results = mcp.query_incidents_semantic("payment_service.py OR database.py", top_k=10)
    print(f"Results: {len(results)}")
    for r in results:
        print(f"  - {r['title']} | files: {r['files_involved']}")
    print()
    
    # Test 5: Filter by files_involved
    print("Test 5: Filter by files_involved collection")
    print("-" * 70)
    try:
        from azure.search.documents.models import SearchFilter
        results = mcp.search_client.search(
            search_text="*",
            filter="files_involved/any(f: f eq 'src/services/payment_service.py')",
            top=10
        )
        docs = list(results)
        print(f"Results: {len(docs)}")
        for doc in docs:
            print(f"  - {doc['title']} | files: {doc['files_involved']}")
    except Exception as e:
        print(f"Filter error: {e}")
    print()


if __name__ == "__main__":
    main()
