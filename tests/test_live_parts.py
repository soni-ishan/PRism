"""
PRism Live Integration Tests — Part by Part
============================================
Tests each component individually using real Azure/GitHub secrets from .env.
Run from project root:
    python -m tests.test_live_parts
"""
import asyncio
import os
import sys
import json
import traceback

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# ── Colour helpers ───────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

def ok(msg):   print(f"  {GREEN}✓ {msg}{RESET}")
def fail(msg): print(f"  {RED}✗ {msg}{RESET}")
def info(msg): print(f"  {CYAN}ℹ {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠ {msg}{RESET}")

results = {}

# ═══════════════════════════════════════════════════════════════════
# 1. AZURE OPENAI
# ═══════════════════════════════════════════════════════════════════
def test_azure_openai():
    print(f"\n{CYAN}{'='*60}")
    print("  1. AZURE OPENAI")
    print(f"{'='*60}{RESET}")
    try:
        from openai import AzureOpenAI
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")

        info(f"Endpoint: {endpoint}")
        info(f"Deployment: {deployment}")

        client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version="2025-01-01-preview",
        )
        resp = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": "Reply with exactly: PRISM_OK"}],
            temperature=0,
            max_tokens=20,
        )
        answer = resp.choices[0].message.content.strip()
        info(f"Response: {answer}")
        ok("Azure OpenAI connection works")
        results["Azure OpenAI"] = True
    except Exception as e:
        fail(f"Azure OpenAI failed: {e}")
        results["Azure OpenAI"] = False


# ═══════════════════════════════════════════════════════════════════
# 2. AZURE AI SEARCH
# ═══════════════════════════════════════════════════════════════════
def test_azure_search():
    print(f"\n{CYAN}{'='*60}")
    print("  2. AZURE AI SEARCH")
    print(f"{'='*60}{RESET}")
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        key = os.getenv("AZURE_SEARCH_KEY")

        info(f"Endpoint: {endpoint}")

        client = SearchClient(
            endpoint=endpoint,
            index_name="incidents",
            credential=AzureKeyCredential(key),
        )
        results_list = list(client.search(search_text="*", top=5))
        info(f"Documents in 'incidents' index: {len(results_list)}")
        for doc in results_list[:3]:
            info(f"  - [{doc.get('severity','?')}] {doc.get('title','N/A')}")
        
        if len(results_list) == 0:
            warn("Index exists but has no documents. Sample data may need to be ingested.")
        
        ok("Azure AI Search connection works")
        results["Azure AI Search"] = True
    except Exception as e:
        fail(f"Azure AI Search failed: {e}")
        traceback.print_exc()
        results["Azure AI Search"] = False


# ═══════════════════════════════════════════════════════════════════
# 3. AZURE CONTENT SAFETY
# ═══════════════════════════════════════════════════════════════════
def test_content_safety():
    print(f"\n{CYAN}{'='*60}")
    print("  3. AZURE CONTENT SAFETY")
    print(f"{'='*60}{RESET}")
    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions
        from azure.core.credentials import AzureKeyCredential

        endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT")
        key = os.getenv("AZURE_CONTENT_SAFETY_KEY")

        info(f"Endpoint: {endpoint}")

        client = ContentSafetyClient(endpoint=endpoint, credential=AzureKeyCredential(key))
        request = AnalyzeTextOptions(text="This is a safe test message for PRism.")
        response = client.analyze_text(request)

        info(f"Hate severity: {response.categories_analysis[0].severity if response.categories_analysis else 'N/A'}")
        ok("Azure Content Safety connection works")
        results["Content Safety"] = True
    except Exception as e:
        fail(f"Azure Content Safety failed: {e}")
        traceback.print_exc()
        results["Content Safety"] = False


# ═══════════════════════════════════════════════════════════════════
# 4. GITHUB PAT
# ═══════════════════════════════════════════════════════════════════
def test_github_pat():
    print(f"\n{CYAN}{'='*60}")
    print("  4. GITHUB PAT")
    print(f"{'='*60}{RESET}")
    try:
        import httpx

        token = os.getenv("GH_PAT")
        repo = os.getenv("GITHUB_REPO", "simarpreet0037/test")

        info(f"Token prefix: {token[:10]}...")
        info(f"Target repo: {repo}")

        # Test 1: authenticated user
        r1 = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
        if r1.status_code == 200:
            user_data = r1.json()
            ok(f"Authenticated as: {user_data.get('login')}")
        else:
            fail(f"Auth check returned {r1.status_code}: {r1.text[:200]}")
            results["GitHub PAT"] = False
            return

        # Test 2: repo access
        r2 = httpx.get(
            f"https://api.github.com/repos/{repo}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
        if r2.status_code == 200:
            ok(f"Repo access confirmed: {repo}")
        else:
            warn(f"Repo access returned {r2.status_code} (may be expected if repo is private/missing)")

        # Test 3: list PRs
        r3 = httpx.get(
            f"https://api.github.com/repos/{repo}/pulls?state=all&per_page=3",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
        if r3.status_code == 200:
            prs = r3.json()
            info(f"PRs found: {len(prs)}")
            for pr in prs[:3]:
                info(f"  - PR #{pr['number']}: {pr['title'][:60]}")
        else:
            warn(f"PR list returned {r3.status_code}")

        ok("GitHub PAT works")
        results["GitHub PAT"] = True
    except Exception as e:
        fail(f"GitHub PAT failed: {e}")
        results["GitHub PAT"] = False


# ═══════════════════════════════════════════════════════════════════
# 5. TIMING AGENT (no external deps)
# ═══════════════════════════════════════════════════════════════════
def test_timing_agent():
    print(f"\n{CYAN}{'='*60}")
    print("  5. TIMING AGENT")
    print(f"{'='*60}{RESET}")
    try:
        from agents.timing_agent import run
        from datetime import datetime, timezone

        # Test with current time
        now = datetime.now(timezone.utc)
        result = asyncio.run(run(now))
        info(f"Timestamp: {now.isoformat()}")
        info(f"Risk modifier: {result.risk_score_modifier}")
        info(f"Status: {result.status}")
        info(f"Findings: {result.findings}")
        info(f"Action: {result.recommended_action}")

        assert result.agent_name == "Timing Agent", f"Wrong agent name: {result.agent_name}"
        assert result.status in ("pass", "warning", "critical"), f"Bad status: {result.status}"
        assert 0 <= result.risk_score_modifier <= 100

        ok("Timing Agent works")
        results["Timing Agent"] = True
    except Exception as e:
        fail(f"Timing Agent failed: {e}")
        traceback.print_exc()
        results["Timing Agent"] = False


# ═══════════════════════════════════════════════════════════════════
# 6. DIFF ANALYST (needs OpenAI)
# ═══════════════════════════════════════════════════════════════════
def test_diff_analyst():
    print(f"\n{CYAN}{'='*60}")
    print("  6. DIFF ANALYST")
    print(f"{'='*60}{RESET}")
    try:
        from agents.diff_analyst import run

        sample_diff = """
diff --git a/payment_service.py b/payment_service.py
index abc1234..def5678 100644
--- a/payment_service.py
+++ b/payment_service.py
@@ -10,8 +10,6 @@ class PaymentService:
     def process_payment(self, amount, currency):
-        try:
-            result = self.gateway.charge(amount, currency)
-            return result
-        except PaymentError as e:
-            logger.error("Payment failed: %s", e)
-            raise
+        result = self.gateway.charge(amount, currency)
+        return result
"""
        changed_files = ["payment_service.py"]

        # run() is async
        result = asyncio.run(run(sample_diff, changed_files))

        info(f"Risk modifier: {result.risk_score_modifier}")
        info(f"Status: {result.status}")
        info(f"Findings ({len(result.findings)}):")
        for f in result.findings[:5]:
            info(f"  - {f[:100]}")
        info(f"Action: {result.recommended_action[:100]}")

        assert result.agent_name == "Diff Analyst"
        assert result.status in ("pass", "warning", "critical")

        ok("Diff Analyst works (with LLM)")
        results["Diff Analyst"] = True
    except Exception as e:
        fail(f"Diff Analyst failed: {e}")
        traceback.print_exc()
        results["Diff Analyst"] = False


# ═══════════════════════════════════════════════════════════════════
# 7. HISTORY AGENT (needs AI Search)
# ═══════════════════════════════════════════════════════════════════
def test_history_agent():
    print(f"\n{CYAN}{'='*60}")
    print("  7. HISTORY AGENT")
    print(f"{'='*60}{RESET}")
    try:
        from agents.history_agent.agent import HistoryAgent

        agent = HistoryAgent()
        info("Connected to Azure AI Search via MCP Server")

        pr_files = ["payment_service.py", "auth_middleware.py", "database.py"]
        result_dict = agent.analyze_pr(pr_files)

        info(f"Risk modifier: {result_dict.get('risk_score_modifier')}")
        info(f"Status: {result_dict.get('status')}")
        findings = result_dict.get("findings", [])
        info(f"Findings ({len(findings)}):")
        for f in findings[:5]:
            info(f"  - {f[:100]}")

        assert result_dict.get("agent_name") == "History Agent"
        assert result_dict.get("status") in ("pass", "warning", "critical")

        ok("History Agent works")
        results["History Agent"] = True
    except Exception as e:
        fail(f"History Agent failed: {e}")
        traceback.print_exc()
        results["History Agent"] = False


# ═══════════════════════════════════════════════════════════════════
# 8. VERDICT AGENT (needs agent results)
# ═══════════════════════════════════════════════════════════════════
def test_verdict_agent():
    print(f"\n{CYAN}{'='*60}")
    print("  8. VERDICT AGENT")
    print(f"{'='*60}{RESET}")
    try:
        from agents.verdict_agent import run as verdict_run
        from agents.shared.data_contract import AgentResult

        # Create mock agent results
        mock_results = [
            AgentResult(
                agent_name="Diff Analyst",
                risk_score_modifier=45,
                status="warning",
                findings=["Removed error handling in payment_service.py"],
                recommended_action="Review error handling changes",
            ),
            AgentResult(
                agent_name="History Agent",
                risk_score_modifier=30,
                status="warning",
                findings=["payment_service.py linked to 2 past incidents"],
                recommended_action="Review incident history",
            ),
            AgentResult(
                agent_name="Coverage Agent",
                risk_score_modifier=0,
                status="pass",
                findings=["No coverage data available"],
                recommended_action="No action needed",
            ),
            AgentResult(
                agent_name="Timing Agent",
                risk_score_modifier=10,
                status="pass",
                findings=["Tuesday deployment during business hours"],
                recommended_action="Good deployment window",
            ),
        ]

        mock_pr = {
            "pr_number": 42,
            "repo": "simarpreet0037/test",
            "changed_files": ["payment_service.py"],
            "diff": "mock diff",
        }

        verdict = asyncio.run(verdict_run(mock_results, mock_pr))

        info(f"Score: {verdict.confidence_score}")
        info(f"Decision: {verdict.decision}")
        info(f"Risk brief length: {len(verdict.risk_brief)} chars")
        if verdict.rollback_playbook:
            info(f"Rollback playbook: {len(verdict.rollback_playbook)} chars")
        info(f"Agent results: {len(verdict.agent_results)}")

        assert verdict.confidence_score >= 0 and verdict.confidence_score <= 100
        assert verdict.decision in ("greenlight", "blocked")

        ok(f"Verdict Agent works — Score={verdict.confidence_score}, Decision={verdict.decision}")
        results["Verdict Agent"] = True
    except Exception as e:
        fail(f"Verdict Agent failed: {e}")
        traceback.print_exc()
        results["Verdict Agent"] = False


# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
def print_summary():
    print(f"\n{CYAN}{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}{RESET}")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, status in results.items():
        if status:
            ok(name)
        else:
            fail(name)

    print(f"\n  {passed}/{total} components passed")
    
    if passed == total:
        print(f"\n  {GREEN}All components healthy! Ready for full Orchestrator test.{RESET}")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"\n  {RED}Failed: {', '.join(failed)}{RESET}")
        print(f"  Fix these before running the full Orchestrator.")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"{CYAN}PRism Live Integration Tests{RESET}")
    print(f"Testing each component with real Azure/GitHub secrets\n")

    test_azure_openai()
    test_azure_search()
    test_content_safety()
    test_github_pat()
    test_timing_agent()
    test_diff_analyst()
    test_history_agent()
    test_verdict_agent()
    print_summary()

    sys.exit(0 if all(results.values()) else 1)
