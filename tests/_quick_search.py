"""Quick test: Azure AI Search"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

endpoint = os.getenv('AZURE_SEARCH_ENDPOINT')
key = os.getenv('AZURE_SEARCH_KEY')
client = SearchClient(endpoint=endpoint, index_name='incidents', credential=AzureKeyCredential(key))
results = list(client.search(search_text='*', top=5))
print(f'Documents found: {len(results)}')
for r in results[:3]:
    sev = r.get('severity', '?')
    title = r.get('title', 'N/A')
    print(f'  [{sev}] {title}')
print('AZURE AI SEARCH: OK')
