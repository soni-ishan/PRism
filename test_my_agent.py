# import asyncio
# # This reaches into your package to find the 'run' function
# from agents.coverage_agent import run 

# async def main():
#     print("🚀 Testing the PRism Coverage Agent...")
#     # We simulate what the Orchestrator would send
#     result = await run(pr_number=1, repo="devDays/PRism")

#     print(f"\n--- Agent Report ---")
#     print(f"Status: {result.status.upper()}")
#     print(f"Risk Score Modifier: +{result.risk_score_modifier}")
#     print(f"Findings: {result.findings}")
#     print(f"Recommendation: {result.recommended_action}")

# if __name__ == "__main__":
#     asyncio.run(main())

########## Updated to match new 'pr_payload' structure that the Orchestrator sends to agents. This is crucial for testing the agent in a way that reflects real usage. The test now simulates a PR with one changed file and checks how the agent processes this input.

# import asyncio
# # This reaches into your package to find the 'run' function
# from agents.coverage_agent import run 

# async def main():
#     print("🚀 Testing the PRism Coverage Agent...")
    
#     # NEW: We wrap everything into a 'pr_payload' dictionary
#     # This is exactly how the PRism Orchestrator sends data!
#     payload = {
#         "pr_number": 1, 
#         "repo": "devDays/PRism",
#         "changed_files": ["math_utils.py"]
#     }

#     # Call the run function with the keyword argument 'pr_payload'
#     result = await run(pr_payload=payload)

#     print(f"\n--- Agent Report ---")
#     print(f"Status: {result.status.upper()}")
#     print(f"Risk Score Modifier: +{result.risk_score_modifier}")
    
#     # Printing findings line-by-line so the Copilot code is easy to read
#     print(f"Findings:")
#     for finding in result.findings:
#         print(f" - {finding}")
        
#     print(f"Recommendation: {result.recommended_action}")

# if __name__ == "__main__":
#     asyncio.run(main())

import asyncio
import os
from agents.coverage_agent import run 

async def main():
    # Make sure your token is loaded
    if not os.environ.get("GITHUB_TOKEN"):
        print("❌ ERROR: GITHUB_TOKEN not found in environment!")
        return

    print("🚀 Running Live API Test...")
    
    # Use a real PR number and your repo name
    # Example: pr_number=1, repo="devDays/PRism"
    try:
        result = await run(pr_number=1, repo="devDays/PRism")

        print(f"\n--- Agent Report ---")
        print(f"Status: {result.status.upper()}")
        print(f"Risk Score Modifier: +{result.risk_score_modifier}")
        print(f"Findings: {result.findings}")
        print(f"Recommendation: {result.recommended_action}")
    except Exception as e:
        print(f"💥 Execution failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())