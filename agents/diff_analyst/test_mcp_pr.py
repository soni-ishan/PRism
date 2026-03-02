import os
from agents.diff_analyst.mcp_client import fetch_pr_diff

OWNER = os.environ["GITHUB_OWNER"]
REPO  = os.environ["GITHUB_REPO"]
PR_NUMBER = 1  # change this to a real PR number

diff_text = fetch_pr_diff(OWNER, REPO, PR_NUMBER)
print(diff_text[:1500])  # print first part only