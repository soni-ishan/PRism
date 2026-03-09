"""
PRism Full Orchestrator Test — End-to-End
==========================================
Runs the full orchestrator pipeline with a realistic PR payload,
using real Azure/GitHub secrets from .env.

Run:  python -m tests.test_live_orchestrator
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SAMPLE_DIFF = """
diff --git a/src/services/payment_service.py b/src/services/payment_service.py
index abc1234..def5678 100644
--- a/src/services/payment_service.py
+++ b/src/services/payment_service.py
@@ -10,12 +10,8 @@ class PaymentService:
     def process_payment(self, amount, currency):
-        try:
-            result = self.gateway.charge(amount, currency)
-            if not result.success:
-                logger.error("Payment failed: %s", result.error)
-                raise PaymentError(result.error)
-            return result
-        except ConnectionError as e:
-            logger.error("Gateway connection failed: %s", e)
-            time.sleep(2)  # backoff
-            return self.gateway.charge(amount, currency)
+        result = self.gateway.charge(amount, currency)
+        return result
 
diff --git a/src/db/database.py b/src/db/database.py
index 111aaa..222bbb 100644
--- a/src/db/database.py
+++ b/src/db/database.py
@@ -5,3 +5,5 @@ class Database:
     def migrate(self):
+        self.execute("ALTER TABLE users DROP COLUMN legacy_id")
+        self.execute("ALTER TABLE payments ALTER COLUMN amount TYPE numeric(20,4)")
         self.execute("CREATE INDEX idx_users_email ON users(email)")
"""

CHANGED_FILES = [
    "src/services/payment_service.py",
    "src/db/database.py",
]


async def main():
    print(f"{CYAN}{'='*60}")
    print("  PRism Full Orchestrator — End-to-End Test")
    print(f"{'='*60}{RESET}\n")

    from agents.orchestrator import PRPayload, orchestrate

    payload = PRPayload(
        pr_number=99,
        repo="simarpreet0037/test",
        changed_files=CHANGED_FILES,
        diff=SAMPLE_DIFF,
    )

    print(f"{CYAN}PR #{payload.pr_number} in {payload.repo}{RESET}")
    print(f"Changed files: {', '.join(payload.changed_files)}")
    print(f"Diff length: {len(payload.diff)} chars\n")

    print(f"{YELLOW}Running orchestrator (all 4 agents in parallel)...{RESET}\n")

    verdict = await orchestrate(payload)

    # ── Print results ─────────────────────────────────────────────
    print(f"\n{CYAN}{'='*60}")
    print("  VERDICT REPORT")
    print(f"{'='*60}{RESET}\n")

    color = GREEN if verdict.decision == "greenlight" else RED
    print(f"  Confidence Score: {verdict.confidence_score}/100")
    print(f"  Decision: {color}{verdict.decision.upper()}{RESET}\n")

    print(f"{CYAN}--- Agent Results ---{RESET}")
    for ar in verdict.agent_results:
        status_color = GREEN if ar.status == "pass" else (YELLOW if ar.status == "warning" else RED)
        print(f"\n  {status_color}[{ar.status.upper()}]{RESET} {ar.agent_name} (risk: {ar.risk_score_modifier})")
        for f in ar.findings[:4]:
            print(f"    - {f[:120]}")
        print(f"    Action: {ar.recommended_action[:120]}")

    print(f"\n{CYAN}--- Risk Brief ---{RESET}")
    # Print first 30 lines
    for line in verdict.risk_brief.split("\n")[:30]:
        print(f"  {line}")

    if verdict.rollback_playbook:
        print(f"\n{CYAN}--- Rollback Playbook ---{RESET}")
        for line in verdict.rollback_playbook.split("\n")[:20]:
            print(f"  {line}")

    # ── Validate ──────────────────────────────────────────────────
    print(f"\n{CYAN}{'='*60}")
    print("  VALIDATION")
    print(f"{'='*60}{RESET}")

    checks = {
        "Confidence score in range": 0 <= verdict.confidence_score <= 100,
        "Decision is valid": verdict.decision in ("greenlight", "blocked"),
        "Has risk brief": len(verdict.risk_brief) > 50,
        "4 agent results": len(verdict.agent_results) == 4,
        "All agents named": all(ar.agent_name for ar in verdict.agent_results),
        "All statuses valid": all(ar.status in ("pass", "warning", "critical") for ar in verdict.agent_results),
    }

    all_ok = True
    for name, passed in checks.items():
        if passed:
            print(f"  {GREEN}✓{RESET} {name}")
        else:
            print(f"  {RED}✗{RESET} {name}")
            all_ok = False

    if all_ok:
        print(f"\n  {GREEN}Orchestrator end-to-end test PASSED!{RESET}")
    else:
        print(f"\n  {RED}Some validations failed.{RESET}")

    return all_ok


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
