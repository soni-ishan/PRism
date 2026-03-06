# agents/diff_analyst/mcp_client.py

import os
import json
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
    This extracts the textual payload safely.
    """
    if hasattr(result, "content") and result.content:
        parts: List[str] = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
        return "\n".join(parts).strip()

    return str(result).strip()


def _get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")   or os.environ.get("GH_PAT") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Missing GitHub token. Set GITHUB_TOKEN or GITHUB_PERSONAL_ACCESS_TOKEN.")
    return token


async def fetch_pr_diff_async(owner: str, repo: str, pr_number: int) -> str:
    """
    Async-first API. Safe to call from FastAPI/orchestrator event loops.
    Uses GitHub MCP server tool: get_pull_request_files
    """
    token = _get_github_token()

    # Provide token under both names to reduce surprises across environments.
    server_env = {
        "GITHUB_TOKEN": token,
        "GH_PAT": token,
        "GITHUB_PERSONAL_ACCESS_TOKEN": token,
        "GITHUB_READ_ONLY": "1",
    }

    server_params = StdioServerParameters(
        command="npx",
        args=["@modelcontextprotocol/server-github"],
        env=server_env,
    )

    exit_stack = AsyncExitStack()
    try:
        read, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        result = await session.call_tool(
            "get_pull_request_files",
            {"owner": owner, "repo": repo, "pull_number": pr_number},
        )

        raw_text = _extract_text_from_tool_result(result)

        # server-github commonly returns JSON as text for file lists
        files: Optional[List[Dict[str, Any]]] = None
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                files = parsed
        except Exception:
            files = None

        # Build unified diff-ish text
        if files is not None:
            chunks: List[str] = []
            for f in files:
                filename = f.get("filename") or f.get("path") or "<unknown>"
                status = f.get("status", "")
                patch = f.get("patch")

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

        # Fallback: return whatever we got (text/objects)
        return raw_text

    finally:
        await exit_stack.aclose()


def fetch_pr_diff(owner: str, repo: str, pr_number: int) -> str:
    """
    Sync helper for local-dev only.

    IMPORTANT:
    If you are already inside an event loop (FastAPI/orchestrator),
    call `await fetch_pr_diff_async(...)` instead.
    """
    try:
        loop = asyncio.get_running_loop()
        # If we get here, we're inside an event loop -> cannot asyncio.run()
        raise RuntimeError(
            "fetch_pr_diff() called inside a running event loop. Use `await fetch_pr_diff_async(...)` instead."
        )
    except RuntimeError as e:
        # Two cases:
        # 1) No running loop -> get_running_loop() raises RuntimeError -> safe to asyncio.run()
        # 2) We raised our own RuntimeError above -> re-raise
        if "no running event loop" in str(e).lower():
            return asyncio.run(fetch_pr_diff_async(owner, repo, pr_number))
        raise