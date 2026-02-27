from agents.diff_analyst.diff_agent import analyze_diff
sample_diff = """
- const API_KEY = "12345"
+ const API_KEY = "12345"
"""

result = analyze_diff(sample_diff)
print(result)