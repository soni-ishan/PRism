import sys
from pathlib import Path

# allow scripts/ to import project modules
sys.path.append(str(Path(__file__).resolve().parents[1]))

import asyncio
import json
import os
from datetime import datetime, timezone

from agents.diff_analyst.mcp_client import fetch_pr_diff_async
from agents.orchestrator import PRPayload, orchestrate

OUTPUT_DIR = Path("prism_output")
OUTPUT_DIR.mkdir(exist_ok=True)


def build_changed_files_from_diff(diff_text: str) -> list[str]:
    files = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                if path.startswith("b/"):
                    path = path[2:]
                files.append(path)
    return list(dict.fromkeys(files))


def render_risk_brief(verdict_data: dict, owner: str, repo: str, pr_number: int) -> str:
    decision = verdict_data.get("decision", "unknown")
    confidence_score = verdict_data.get("confidence_score", "N/A")
    risk_brief = (verdict_data.get("risk_brief") or "").strip()

    parts = [
        "## PRism Risk Brief",
        "",
        f"**Repository:** `{owner}/{repo}`  ",
        f"**PR:** #{pr_number}  ",
        f"**Decision:** `{decision}`  ",
        f"**Confidence Score:** `{confidence_score}`  ",
        "",
    ]

    if risk_brief:
        parts.append(risk_brief)
        parts.append("")
    else:
        parts.extend([
            "### Notes",
            "No risk brief generated.",
            "",
        ])

    parts.extend([
        "### Notes",
        "Generated automatically by PRism during CI.",
    ])

    return "\n".join(parts)


async def main() -> None:
    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]
    pr_number = int(os.environ["PR_NUMBER"])

    print(f"Running PRism on {owner}/{repo} PR #{pr_number}")

    diff_text = await fetch_pr_diff_async(owner, repo, pr_number)
    changed_files = build_changed_files_from_diff(diff_text)

    payload = PRPayload(
        pr_number=pr_number,
        repo=f"{owner}/{repo}",
        changed_files=changed_files,
        diff=diff_text,
        timestamp=datetime.now(timezone.utc),
    )

    verdict = await orchestrate(payload)

    if hasattr(verdict, "model_dump"):
        verdict_data = verdict.model_dump()
    elif hasattr(verdict, "dict"):
        verdict_data = verdict.dict()
    else:
        raise TypeError(f"Unexpected verdict type: {type(verdict)}")

    risk_brief_md = render_risk_brief(verdict_data, owner, repo, pr_number)

    (OUTPUT_DIR / "verdict.json").write_text(
        json.dumps(verdict_data, indent=2, default=str),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "risk_brief.md").write_text(
        risk_brief_md,
        encoding="utf-8",
    )

    print(f"Decision: {verdict_data.get('decision')}")
    print("Saved prism_output/verdict.json and prism_output/risk_brief.md")


if __name__ == "__main__":
    asyncio.run(main())