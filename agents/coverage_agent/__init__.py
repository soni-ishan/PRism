import os
import ast
import subprocess # To run terminal commands
from agents.shared.data_contract import AgentResult

async def run(pr_number: int, repo: str) -> AgentResult:
    source_file = "math_utils.py"
    test_file = "tests/test_math_utils.py"
    
    findings = []
    risk_score = 0
    status = "pass"

    if os.path.exists(source_file):
        with open(source_file, "r") as f:
            tree = ast.parse(f.read())
        source_funcs = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]

        if not os.path.exists(test_file):
            missing = source_funcs
        else:
            with open(test_file, "r") as f:
                test_content = f.read()
            missing = [f for f in source_funcs if f not in test_content]

        if missing:
            status = "warning"
            risk_score = 25
            findings.append(f"Missing tests for: {missing}")
            
            # --- THE AUTO-FIXER ACTION ---
            print(f"🤖 Gap detected! Asking Copilot to generate tests for {missing}...")
            
            # This command tells the Copilot CLI to write the tests
            # Note: This assumes you have the 'gh' CLI and 'copilot' extension installed
            prompt = f"Generate pytest unit tests for the following functions in {source_file}: {missing}"
            try:
                # We use 'gh copilot' to generate the code
                subprocess.run(["gh", "copilot", "suggest", prompt], check=True)
                findings.append("✅ Copilot suggested a fix. Manual review required.")
            except Exception as e:
                findings.append(f"❌ Could not trigger Copilot fix: {str(e)}")
        else:
            findings.append(f"All functions in {source_file} are covered.")

    return AgentResult(
        agent_name="Coverage Agent",
        risk_score_modifier=risk_score,
        status=status,
        findings=findings,
        recommended_action="Review auto-generated tests in the PR." if missing else "No action needed."
    )