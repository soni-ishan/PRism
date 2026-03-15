"""
PRism Azure Function App for incident ingestion.

Triggers:
1) Event Grid trigger: ingest on Azure Monitor alerts (all registered repos)
2) Timer trigger: scheduled pull from Log Analytics -> Azure AI Search (all repos)
3) HTTP trigger: manual/ops backfill invocation (all repos or single repo)
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path so mcp_servers.* is importable
# when this file is run as a standalone Azure Function.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import azure.functions as func

from mcp_servers.azure_mcp_server.ingest import (
    ingest_from_alert,
    ingest_from_logs,
    ingest_all_repos,
    ingest_alert_all_repos,
)

logger = logging.getLogger("prism.azure_function")
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.event_grid_trigger(arg_name="event")
async def ingest_from_monitor_alert_trigger(event: func.EventGridEvent) -> None:
    """Push Monitor-alert-driven incidents into AI Search for all registered repos."""
    try:
        payload = event.get_json()
        incidents = await ingest_alert_all_repos(payload)
        logger.info(
            "Alert ingest complete: %d incidents pushed across all repos",
            len(incidents),
        )
    except Exception as exc:
        logger.exception("Event Grid ingest failed: %s", exc)
        raise


@app.timer_trigger(schedule="0 */10 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
async def ingest_logs_timer(timer: func.TimerRequest) -> None:
    """
    Scheduled ingestion from Log Analytics for all registered repos.
    Default cadence: every 10 minutes.
    """
    try:
        window_minutes = int(os.getenv("AZURE_INGEST_WINDOW_MINUTES", "30"))
        fired_time = _utc_now_iso()

        summary = await ingest_all_repos(
            fired_time=fired_time,
            window_minutes=window_minutes,
        )
        logger.info(
            "Timer ingest complete: repos=%d skipped=%d fetched=%d prepared=%d pushed=%d",
            summary["repos_processed"],
            summary["repos_skipped"],
            summary["total_fetched"],
            summary["total_prepared"],
            summary["total_pushed"],
        )
    except Exception as exc:
        logger.exception("Timer ingest failed: %s", exc)
        raise


@app.route(route="ingest/logs", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
async def ingest_logs_http(req: func.HttpRequest) -> func.HttpResponse:
    """Manual trigger for on-demand/backfill ingestion.

    Body (all optional):
        fired_time:     ISO-8601 timestamp (default: now)
        window_minutes: Query window (default: 30)
        owner:          GitHub owner — if set with repo, ingest single repo only
        repo:           GitHub repo  — if set with owner, ingest single repo only
    """
    try:
        body = req.get_json()
    except ValueError:
        body = {}

    fired_time = body.get("fired_time") or req.params.get("fired_time") or _utc_now_iso()
    window_minutes = int(body.get("window_minutes") or req.params.get("window_minutes") or os.getenv("AZURE_INGEST_WINDOW_MINUTES", "30"))
    owner = body.get("owner") or req.params.get("owner") or ""
    repo = body.get("repo") or req.params.get("repo") or ""

    try:
        if owner and repo:
            # Single-repo mode: look up registration from DB
            from mcp_servers.azure_mcp_server.ingest import fetch_all_registrations, _derive_index_name
            registrations = await fetch_all_registrations()
            reg = next(
                (r for r in registrations if r["owner"] == owner and r["repo"] == repo),
                None,
            )
            if not reg:
                return func.HttpResponse(
                    json.dumps({"error": f"No active registration found for {owner}/{repo}"}),
                    mimetype="application/json",
                    status_code=404,
                )
            summary = await ingest_from_logs(
                workspace_id=reg["azure_customer_id"],
                fired_time=fired_time,
                window_minutes=window_minutes,
                tenant_id=reg["azure_tenant_id"] or None,
                index_name=reg["index_name"],
            )
        else:
            # All-repos mode
            summary = await ingest_all_repos(
                fired_time=fired_time,
                window_minutes=window_minutes,
            )
    except (RuntimeError, ValueError, TypeError) as exc:
        logger.warning("Client error in log ingestion: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Configuration error", "details": str(exc)}),
            mimetype="application/json",
            status_code=400,
        )
    except Exception as exc:
        logger.exception("Server error in log ingestion: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Internal server error", "details": "Processing failed. Please check logs for details."}),
            mimetype="application/json",
            status_code=500,
        )

    return func.HttpResponse(
        json.dumps(
            {
                "status": "ok",
                "fired_time": fired_time,
                "summary": summary,
            }
        ),
        mimetype="application/json",
        status_code=200,
    )
