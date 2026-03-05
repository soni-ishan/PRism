import os
from agents.diff_analyst.diff_agent import run_from_pr

# put a real PR number here
PR_NUMBER = 1

print(run_from_pr(PR_NUMBER).to_json())