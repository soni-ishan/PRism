"""
PRism Azure Function App for incident ingestion.

Triggers:
1) Event Grid trigger: ingest on Azure Monitor alerts
2) Timer trigger: scheduled pull from Log Analytics -> Azure AI Search
3) HTTP trigger: manual/ops backfill invocation
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func

from mcp_servers.azure_mcp_server.ingest import ingest_from_alert, ingest_from_logs

logger = logging.getLogger("prism.azure_function")
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_log_ingest(fired_time: str | None = None) -> dict[str, int]:
    workspace_id = os.getenv("AZURE_LOG_WORKSPACE_ID", "")
    if not workspace_id:
        raise RuntimeError("AZURE_LOG_WORKSPACE_ID is required")

    window_minutes = int(os.getenv("AZURE_INGEST_WINDOW_MINUTES", "30"))

    return asyncio.run(
        ingest_from_logs(
            workspace_id=workspace_id,
            fired_time=fired_time,
            window_minutes=window_minutes,
        )
    )


@app.event_grid_trigger(arg_name="event")
def ingest_from_monitor_alert(event: func.EventGridEvent) -> None:
    """Push Monitor-alert-driven incidents into Azure AI Search."""
    try:
        payload = event.get_json()
        incident = asyncio.run(ingest_from_alert(payload))
        if incident:
            logger.info("Alert ingest succeeded for incident_id=%s", incident.get("id"))
        else:
            logger.info("Alert ingest produced no incident document")
    except Exception as exc:
        logger.exception("Event Grid ingest failed: %s", exc)
        raise


@app.timer_trigger(schedule="0 */10 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def ingest_logs_timer(timer: func.TimerRequest) -> None:
    """
    Independent scheduled ingestion from Log Analytics.
    Default cadence: every 10 minutes.
    """
    try:
        summary = _run_log_ingest(fired_time=_utc_now_iso())
        logger.info(
            "Timer ingest complete fetched=%d prepared=%d pushed=%d",
            summary["fetched"],
            summary["prepared"],
            summary["pushed"],
        )
    except Exception as exc:
        logger.exception("Timer ingest failed: %s", exc)
        raise


@app.route(route="ingest/logs", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def ingest_logs_http(req: func.HttpRequest) -> func.HttpResponse:
    """Manual trigger for on-demand/backfill ingestion."""
    try:
        body = req.get_json()
    except ValueError:
        body = {}

    fired_time = body.get("fired_time") or req.params.get("fired_time") or _utc_now_iso()

    try:
        summary = _run_log_ingest(fired_time=fired_time)
    except Exception as exc:
        return func.HttpResponse(str(exc), status_code=400)

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
