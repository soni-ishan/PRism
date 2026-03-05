# agents/diff_analyst/mcp_client.py
import os
import asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()


def _extract_text_from_tool_result(result: Any) -> str:
    """
    MCP tool results often come back as content blocks.
    This tries to extract the textual payload safely.
    """
    if hasattr(result, "content") and result.content:
        parts = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
        return "\n".join(parts).strip()

    return str(result)


async def fetch_pr_diff_async(owner: str, repo: str, pr_number: int) -> str:
    token = os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]

    server_params = StdioServerParameters(
        command="npx",
        args=["@modelcontextprotocol/server-github"],
        env={
            "GITHUB_PERSONAL_ACCESS_TOKEN": token,
            "GITHUB_READ_ONLY": "1",
        },
    )

    exit_stack = AsyncExitStack()
    try:
        read, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        # 1) Fetch PR files (includes patches for most text files)
        result = await session.call_tool(
            "get_pull_request_files",
            {"owner": owner, "repo": repo, "pull_number": pr_number},
        )

        raw_text = _extract_text_from_tool_result(result)

        # The server-github tool commonly returns JSON as text.
        # We'll parse it if possible; otherwise we'll just pass raw text through.
        files: Optional[List[Dict[str, Any]]] = None
        try:
            import json
            files = json.loads(raw_text)
        except Exception:
            files = None

        # 2) Build unified diff-ish text
        if isinstance(files, list):
            chunks = []
            for f in files:
                filename = f.get("filename") or f.get("path") or "<unknown>"
                status = f.get("status", "")
                patch = f.get("patch")

                # Some files (binary/large) may have patch=None.
                if patch:
                    chunks.append(
                        f"diff --git a/{filename} b/{filename}\n"
                        f"# status: {status}\n"
                        f"{patch}\n"
                    )
                else:
                    chunks.append(
                        f"diff --git a/{filename} b/{filename}\n"
                        f"# status: {status}\n"
                        f"# (No patch provided: file may be binary, too large, or GitHub omitted patch.)\n"
                    )
            return "\n".join(chunks).strip()

        # Fallback: return whatever we got
        return raw_text

    finally:
        await exit_stack.aclose()


def fetch_pr_diff(owner: str, repo: str, pr_number: int) -> str:
    return asyncio.run(fetch_pr_diff_async(owner, repo, pr_number))