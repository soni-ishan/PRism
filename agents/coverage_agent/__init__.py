# import os
# import ast
# import subprocess
# from typing import Any
# from agents.shared.data_contract import AgentResult

# async def run(pr_payload: dict[str, Any] = None) -> AgentResult:
#     """
#     PRism Coverage Agent - Aligned to System Rules.
#     """
#     # 1. PRism Standard: Get files from the payload
#     if pr_payload is None:
#         pr_payload = {}
#     changed_files = pr_payload.get("changed_files", ["math_utils.py"])
    
#     findings = []
#     missing_tests = []
    
#     # 2. YOUR ORIGINAL LOGIC: The AST Scanner
#     for source_file in changed_files:
#         if not source_file.endswith(".py") or not os.path.exists(source_file):
#             continue
            
#         # [Scanning math_utils.py for functions...]
#         with open(source_file, "r") as f:
#             tree = ast.parse(f.read())
            
#         functions = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        
#         # [Checking if test_math_utils.py exists and has those functions...]
#         test_file = f"tests/test_{source_file}"
#         if not os.path.exists(test_file):
#             missing_tests.extend(functions)
#         else:
#             with open(test_file, "r") as f:
#                 test_content = f.read()
#                 for func in functions:
#                     if f"test_{func}" not in test_content:
#                         missing_tests.append(func)

#     # 3. PRism Rules: Calculate Score & Status
#     risk_score = 0
#     status = "pass"
    
#     if missing_tests:
#         # Rules: +25 modifier for missing unit tests
#         risk_score = 25 
#         status = "warning"
#         findings.append(f"Gap detected! Missing tests for: {missing_tests}")
        
#         # Trigger the "Agentic" part (Copilot)
#         # Note: We keep the install message check you liked!
#         try:
#             subprocess.run(["copilot", "--prompt", f"Write pytest tests for the functions: {missing_tests}"], check=False)
#             findings.append("✅ Copilot suggested a fix. Manual review required.")
#         except:
#             findings.append("❌ Copilot CLI not found. Manual test generation needed.")

#     # 4. Data Contract Compliance: Return the validated object
#     return AgentResult(
#         agent_name="Coverage Agent",
#         risk_score_modifier=max(0, min(100, risk_score)), # Rule: Clamp 0-100
#         status=status, # Rule: Literal "pass", "warning", "critical"
#         findings=findings,
#         recommended_action="Review auto-generated tests and merge into the PR."
#     )
############

import os
import httpx
from typing import Any
from agents.shared.data_contract import AgentResult

AGENT_NAME = "Coverage Agent"

async def run(pr_number: int, repo: str) -> AgentResult:
    """
    Evaluates test coverage risk by checking if changed files have matching tests.
    If coverage is low, it triggers a GitHub issue for Copilot remediation.
    """
    github_token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json"
    }
    
    findings = []
    risk_score = 0
    
    async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
        try:
            # a) Fetch the PR's changed files
            files_url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files"
            resp = await client.get(files_url)
            resp.raise_for_status()
            changed_files = resp.json()

            for file_data in changed_files:
                filename = file_data["filename"]
                status = file_data["status"] # 'added', 'modified', 'removed'

                # Skip non-python files
                if not filename.endswith(".py"):
                    continue

                # b) logic: check for matching test file
                # Path logic: agents/foo.py -> tests/test_foo.py
                path_parts = filename.split("/")
                base_name = path_parts[-1]
                test_filename = f"tests/test_{base_name}"
                
                # Check if it's a deleted test file
                if "tests/" in filename and status == "removed":
                    risk_score += 25
                    findings.append(f"Deleted test file: {filename}")
                    continue

                # Check existence of corresponding test for source files
                if "tests/" not in filename:
                    check_url = f"https://api.github.com/repos/{repo}/contents/{test_filename}"
                    test_resp = await client.get(check_url)
                    
                    if test_resp.status_code == 404:
                        # c) Risk Calculation
                        risk_score += 15
                        findings.append(f"No test file found for {filename} (Expected {test_filename})")

            # d) Final Score Caps and Status
            risk_score = min(risk_score, 100)
            
            if risk_score <= 20:
                agent_status = "pass"
                recommended_action = "Coverage looks good. Proceed."
            elif risk_score <= 50:
                agent_status = "warning"
                recommended_action = "Test coverage is lacking for some files. Review suggested."
                await _trigger_copilot_issue(client, repo, pr_number, findings)
            else:
                agent_status = "critical"
                recommended_action = "Significant coverage regression. Blocking deployment."
                await _trigger_copilot_issue(client, repo, pr_number, findings)

            if not findings:
                findings.append("All changed files have corresponding test files.")

            return AgentResult(
                agent_name=AGENT_NAME,
                risk_score_modifier=risk_score,
                status=agent_status,
                findings=findings,
                recommended_action=recommended_action
            )

        except Exception as e:
            # Graceful fallback per requirements
            return AgentResult(
                agent_name=AGENT_NAME,
                risk_score_modifier=50,
                status="warning",
                findings=[f"API Error: {str(e)}"],
                recommended_action="Manual coverage check required due to API failure."
            )

async def _trigger_copilot_issue(client: httpx.AsyncClient, repo: str, pr_number: int, findings: list[str]):
    """Stretch Goal: Create a GitHub Issue for Copilot to fix tests."""
    issue_url = f"https://api.github.com/repos/{repo}/issues"
    body = "The following files require unit tests to maintain coverage:\n\n" + "\n".join(findings)
    
    payload = {
        "title": f"PRism: Auto-generate tests for PR #{pr_number}",
        "body": body,
        "labels": ["copilot-remediate"]
    }
    await client.post(issue_url, json=payload)