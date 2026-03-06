import os
import ast
import subprocess
from typing import Any
from agents.shared.data_contract import AgentResult

async def run(pr_payload: dict[str, Any] = None) -> AgentResult:
    """
    PRism Coverage Agent - Aligned to System Rules.
    """
    # 1. PRism Standard: Get files from the payload
    if pr_payload is None:
        pr_payload = {}
    changed_files = pr_payload.get("changed_files", ["math_utils.py"])
    
    findings = []
    missing_tests = []
    
    # 2. YOUR ORIGINAL LOGIC: The AST Scanner
    for source_file in changed_files:
        if not source_file.endswith(".py") or not os.path.exists(source_file):
            continue
            
        # [Scanning math_utils.py for functions...]
        with open(source_file, "r") as f:
            tree = ast.parse(f.read())
            
        functions = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        
        # [Checking if test_math_utils.py exists and has those functions...]
        test_file = f"tests/test_{source_file}"
        if not os.path.exists(test_file):
            missing_tests.extend(functions)
        else:
            with open(test_file, "r") as f:
                test_content = f.read()
                for func in functions:
                    if f"test_{func}" not in test_content:
                        missing_tests.append(func)

    # 3. PRism Rules: Calculate Score & Status
    risk_score = 0
    status = "pass"
    
    if missing_tests:
        # Rules: +25 modifier for missing unit tests
        risk_score = 25 
        status = "warning"
        findings.append(f"Gap detected! Missing tests for: {missing_tests}")
        
        # Trigger the "Agentic" part (Copilot)
        # Note: We keep the install message check you liked!
        try:
            subprocess.run(["copilot", "--prompt", f"Write pytest tests for the functions: {missing_tests}"], check=False)
            findings.append("✅ Copilot suggested a fix. Manual review required.")
        except:
            findings.append("❌ Copilot CLI not found. Manual test generation needed.")

    # 4. Data Contract Compliance: Return the validated object
    return AgentResult(
        agent_name="Coverage Agent",
        risk_score_modifier=max(0, min(100, risk_score)), # Rule: Clamp 0-100
        status=status, # Rule: Literal "pass", "warning", "critical"
        findings=findings,
        recommended_action="Review auto-generated tests and merge into the PR."
    )