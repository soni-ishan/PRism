from .llm_client import call_llm

def analyze_diff(diff_text: str) -> str:
    system_prompt = """
You are the Diff Analyst Agent.

You MUST return ONLY valid JSON matching this exact schema:

{
  "agent_name": "Diff Analyst Agent",
  "risk_score_modifier": 0,
  "status": "pass",
  "findings": [],
  "recommended_action": ""
}

Rules:
- risk_score_modifier must be integer 0-100
- status must be "pass", "warning", or "critical"
- Output ONLY JSON. No markdown. No explanation.
"""

    result = call_llm(system_prompt, diff_text)
    return result