import os
import sys
from agents.diff_analyst.diff_agent import run_from_pr

pr_number = int(sys.argv[1])
result = run_from_pr(pr_number)
print(result.to_json())