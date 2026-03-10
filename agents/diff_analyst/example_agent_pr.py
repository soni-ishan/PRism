import sys
from agents.diff_analyst.diff_agent import run_from_pr


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m agents.diff_analyst.example_agent_pr <pr_number>")
        return

    pr_number = int(sys.argv[1])

    try:
        result = run_from_pr(pr_number)
        print(f"PR #{pr_number}")
        print(result.to_json())
    except Exception as e:
        print(f"PR #{pr_number} failed: {e}")


if __name__ == "__main__":
    main()