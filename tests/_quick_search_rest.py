"""Quick test: Azure AI Search — use REST to bypass SDK issues"""
import os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()

endpoint = os.getenv('AZURE_SEARCH_ENDPOINT').rstrip('/')
key = os.getenv('AZURE_SEARCH_KEY')

# List indexes
print("Listing indexes...")
url = f"{endpoint}/indexes?api-version=2023-11-01"
headers = {"api-key": key, "Content-Type": "application/json"}
r = requests.get(url, headers=headers, timeout=15)
print(f"Status: {r.status_code}")
if r.status_code == 200:
    indexes = r.json().get("value", [])
    print(f"Indexes: {[idx['name'] for idx in indexes]}")
    
    # Query incidents index
    if any(idx['name'] == 'incidents' for idx in indexes):
        print("\nQuerying incidents index...")
        search_url = f"{endpoint}/indexes/incidents/docs/search?api-version=2023-11-01"
        body = {"search": "*", "top": 5}
        r2 = requests.post(search_url, headers=headers, json=body, timeout=15)
        print(f"Search status: {r2.status_code}")
        if r2.status_code == 200:
            docs = r2.json().get("value", [])
            print(f"Documents: {len(docs)}")
            for d in docs[:3]:
                print(f"  [{d.get('severity','?')}] {d.get('title','N/A')}")
            print("AZURE AI SEARCH: OK")
        else:
            print(f"Search error: {r2.text[:300]}")
    else:
        print("No 'incidents' index found - need to create it")
else:
    print(f"Error: {r.text[:300]}")
