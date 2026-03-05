from agents.diff_analyst.diff_agent import run_from_pr

for i in range(1, 7):
    print(run_from_pr(i).to_json())