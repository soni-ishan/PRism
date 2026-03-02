"""
PRism Incident Ingest
=====================
Event-driven ingestion: triggered by Azure Monitor alerts.
Queries App Insights for exception details, uses Azure OpenAI
to extract source file paths, and pushes a structured incident
document to Azure AI Search.

This file is independent of setup.py and query.py.
It only WRITES to AI Search — never reads.

Deployed as an Azure Function or called directly.
"""

import json
import os
import logging
from datetime import timedelta
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.search.documents import SearchClient
from openai import AsyncAzureOpenAI

logger = logging.getLogger("prism.ingest")


# ── Configuration ────────────────────────────────────────────

SEVERITY_MAP = {
    "Sev0": "critical",
    "Sev1": "critical",
    "Sev2": "high",
    "Sev3": "medium",
    "Sev4": "low",
}

LLM_SYSTEM_PROMPT = """You extract application source code file paths from exception stack traces.

Rules:
- Return ONLY paths that are application source code written by the developer
- EXCLUDE framework files (fastapi, django, flask, express, ASP.NET, Spring Boot, etc.)
- EXCLUDE standard library files (python stdlib, .NET BCL, java.lang, etc.)
- EXCLUDE third-party packages (site-packages, node_modules, vendor, .nuget, pkg/mod)
- Strip deployment path prefixes: /app/, /home/site/wwwroot/, /var/task/
- Return paths relative to the repo root (e.g. "src/services/payment.py")
- If the trace is minified/bundled and you cannot determine real source files, return empty
- If there is no stack trace, return empty

Return ONLY valid JSON: {"files": ["path1", "path2"]}"""


# ── File Extraction (LLM) ───────────────────────────────────

async def extract_files(stacktrace: str, error_message: str = "") -> list[str]:
    """
    Use Azure OpenAI to extract source file paths from a stack trace.

    Returns:
        Clean list of relative file paths. Empty list if extraction fails.
    """
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")

    if not endpoint or not deployment:
        logger.warning("Azure OpenAI not configured — cannot extract files")
        return []

    if not stacktrace and not error_message:
        return []

    try:
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            credential=DefaultAzureCredential(),
            api_version="2024-12-01-preview",
        )

        user_content = ""
        if error_message:
            user_content += f"Error: {error_message}\n\n"
        if stacktrace:
            user_content += f"Stack trace:\n{stacktrace}"

        response = await client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        files = result.get("files", [])

        # Sanity check — reject obvious hallucinations
        cleaned = [f for f in files if _looks_like_real_file(f)]

        logger.info("Extracted %d files from stack trace", len(cleaned))
        return cleaned

    except Exception as exc:
        logger.warning("LLM file extraction failed: %s", exc)
        return []


def _looks_like_real_file(path: str) -> bool:
    """Reject obvious LLM hallucinations."""
    if not isinstance(path, str) or len(path) == 0:
        return False
    if " " in path:              # Paths don't have spaces
        return False
    if len(path) > 200:          # Absurdly long
        return False
    if path.startswith("http"):  # URL, not a file
        return False
    # Must have something that looks like a file extension
    basename = path.rsplit("/", 1)[-1]
    if "." not in basename:
        return False
    return True


# ── App Insights Query ───────────────────────────────────────

async def fetch_exceptions(
    workspace_id: str,
    resource_name: str,
    fired_time: str,
    window_minutes: int = 10,
) -> list[dict[str, Any]]:
    """
    Query Application Insights for recent exceptions on a resource.

    Returns:
        List of dicts with keys: timestamp, exception_type, message, stacktrace
    """
    credential = DefaultAzureCredential()
    logs_client = LogsQueryClient(credential)

    kql = f"""
    exceptions
    | where timestamp >= datetime('{fired_time}') - {window_minutes}m
    | where timestamp <= datetime('{fired_time}') + 5m
    | where cloud_RoleName == '{resource_name}'
    | where severityLevel >= 3
    | project
        timestamp,
        exceptionType = type,
        message = outerMessage,
        stackTrace = tostring(details[0].rawStack),
        serviceName = cloud_RoleName
    | top 5 by timestamp desc
    """

    try:
        result = logs_client.query_workspace(
            workspace_id=workspace_id,
            query=kql,
            timespan=timedelta(hours=1),
        )

        if result.status != LogsQueryStatus.SUCCESS or not result.tables:
            logger.warning("App Insights query returned no results")
            return []

        exceptions = []
        for row in result.tables[0].rows:
            exceptions.append({
                "timestamp": str(row[0]),
                "exception_type": str(row[1] or ""),
                "message": str(row[2] or ""),
                "stacktrace": str(row[3] or ""),
                "service_name": str(row[4] or ""),
            })

        logger.info("Fetched %d exceptions from App Insights", len(exceptions))
        return exceptions

    except Exception as exc:
        logger.warning("Failed to query App Insights: %s", exc)
        return []


# ── Push to AI Search ────────────────────────────────────────

def push_incident(incident: dict[str, Any]) -> bool:
    """
    Push a single incident document to Azure AI Search.

    Returns True on success, False on failure.
    """
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        logger.error("AZURE_SEARCH_ENDPOINT not set — cannot push incident")
        return False

    try:
        credential = DefaultAzureCredential()
        search_client = SearchClient(
            endpoint=endpoint,
            index_name="incidents",
            credential=credential,
        )

        result = search_client.upload_documents(documents=[incident])
        succeeded = sum(1 for r in result if r.succeeded)

        if succeeded == 1:
            logger.info("Pushed incident %s to AI Search", incident.get("id"))
            return True
        else:
            logger.warning("Failed to push incident %s", incident.get("id"))
            return False

    except Exception as exc:
        logger.error("AI Search push failed: %s", exc)
        return False


# ── Main Ingestion Pipeline ──────────────────────────────────

def _extract_resource_name(resource_id: str) -> str:
    """Extract the resource name from an Azure resource ID."""
    # /subscriptions/.../providers/Microsoft.App/containerApps/payment-api
    # → "payment-api"
    return resource_id.rstrip("/").rsplit("/", 1)[-1] if resource_id else "unknown"


async def ingest_from_alert(alert_payload: dict[str, Any]) -> dict[str, Any] | None:
    """
    Full ingestion pipeline: alert → App Insights → LLM → AI Search.

    Args:
        alert_payload: The raw Azure Monitor alert JSON (from Event Grid).

    Returns:
        The incident document that was pushed, or None if ingestion failed.
    """
    # ── Parse alert ──────────────────────────────────────
    essentials = alert_payload.get("data", {}).get("essentials", {})

    resource_id = essentials.get("targetResourceId", "")
    resource_name = _extract_resource_name(resource_id)
    severity_raw = essentials.get("severity", "Sev3")
    severity = SEVERITY_MAP.get(severity_raw, "medium")
    fired_time = essentials.get("firedDateTime", "")
    alert_name = essentials.get("alertRule", "Unknown alert")
    alert_id = essentials.get("alertId", "unknown")

    logger.info(
        "Processing alert: %s on %s at %s",
        alert_name, resource_name, fired_time,
    )

    # ── Fetch exceptions from App Insights ───────────────
    workspace_id = os.getenv("AZURE_LOG_WORKSPACE_ID", "")

    files_involved: list[str] = []
    error_message = ""

    if workspace_id:
        exceptions = await fetch_exceptions(
            workspace_id=workspace_id,
            resource_name=resource_name,
            fired_time=fired_time,
        )

        # Extract files from the stack traces
        for exc in exceptions:
            error_message = error_message or exc["message"]
            if exc["stacktrace"]:
                files = await extract_files(exc["stacktrace"], exc["message"])
                files_involved.extend(files)

        # Deduplicate
        files_involved = sorted(set(files_involved))

    # ── Skip if no files found ───────────────────────────
    if not files_involved:
        logger.info(
            "No source files extracted for alert %s — skipping ingestion",
            alert_name,
        )
        return None

    # ── Build incident document ──────────────────────────
    # Short unique ID from the alert ID
    short_id = alert_id.rsplit("/", 1)[-1][:12] if "/" in alert_id else alert_id[:12]

    incident = {
        "id": f"INC-auto-{short_id}",
        "timestamp": fired_time,
        "title": alert_name,
        "severity": severity,
        "files_involved": files_involved,
        "error_message": error_message,
        "root_cause": "",  # Filled later by post-mortem or AI
        "affected_services": [resource_name],
        "duration_minutes": 0,  # Updated when alert resolves
    }

    # ── Push to AI Search ────────────────────────────────
    success = push_incident(incident)
    return incident if success else None