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
import argparse
import hashlib
import asyncio
import re
from datetime import timedelta, datetime, timezone
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.search.documents import SearchClient
from openai import AsyncAzureOpenAI

logger = logging.getLogger("prism.ingest")

INDEX_NAME = "incidents"


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
        logger.info("Azure OpenAI not configured — using regex-based file extraction")
        return _extract_files_from_text(stacktrace, error_message)

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
        logger.warning("LLM file extraction failed, using regex fallback: %s", exc)
        return _extract_files_from_text(stacktrace, error_message)


def _extract_files_from_text(stacktrace: str, error_message: str = "") -> list[str]:
    """Best-effort extraction for source file paths without LLM."""
    text = "\n".join(part for part in [stacktrace, error_message] if part)
    if not text:
        return []

    # Examples matched:
    #   File "/app/src/services/payment.py", line 10
    #   at src/services/payment.ts:12:4
    #   /home/site/wwwroot/src/api/orders.js:42
    file_patterns = [
        r"File\s+[\"']([^\"']+\.[a-zA-Z0-9]+)[\"']",
        r"(?:^|\s)(/[^\s:\"']+\.[a-zA-Z0-9]+)(?::\d+)?",
        r"(?:^|\s)([A-Za-z0-9_./\\-]+\.[a-zA-Z0-9]+)(?::\d+)?",
    ]

    extracted: list[str] = []
    for pattern in file_patterns:
        for match in re.findall(pattern, text, flags=re.MULTILINE):
            candidate = match.replace("\\", "/").strip()
            candidate = _normalize_repo_relative_path(candidate)
            if _looks_like_real_file(candidate):
                extracted.append(candidate)

    # Keep order stable while deduplicating
    deduped = list(dict.fromkeys(extracted))
    return deduped


def _normalize_repo_relative_path(path: str) -> str:
    """Trim common deployment/runtime prefixes and leading path noise."""
    normalized = path.strip().replace("\\", "/")
    prefixes = [
        "/app/",
        "/home/site/wwwroot/",
        "/var/task/",
        "./",
    ]
    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    return normalized.lstrip("/")


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

    key = os.getenv("AZURE_SEARCH_KEY")

    try:
        credential = AzureKeyCredential(key) if key else DefaultAzureCredential()
        search_client = SearchClient(
            endpoint=endpoint,
            index_name=INDEX_NAME,
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


def _build_incident_from_exception(
    exception_data: dict[str, Any],
    title_prefix: str = "Auto-ingested incident",
) -> dict[str, Any]:
    """Map an exception log row to the incidents index schema."""
    timestamp = exception_data.get("timestamp", "")
    service_name = exception_data.get("service_name", "unknown-service")
    exception_type = exception_data.get("exception_type", "")
    message = exception_data.get("message", "")
    files_involved = exception_data.get("files_involved", [])

    identity_material = f"{timestamp}|{service_name}|{exception_type}|{message}".encode("utf-8")
    short_hash = hashlib.sha1(identity_material).hexdigest()[:12]

    return {
        "id": f"INC-log-{short_hash}",
        "timestamp": timestamp,
        "title": f"{title_prefix}: {service_name}",
        "severity": "high",
        "files_involved": files_involved,
        "error_message": message,
        "root_cause": "",
        "affected_services": [service_name],
        "duration_minutes": 0,
    }


async def ingest_from_logs(
    workspace_id: str,
    resource_name: str,
    fired_time: str | None = None,
    window_minutes: int = 30,
) -> dict[str, int]:
    """
    Independent Azure-native ingestion path:
    Log Analytics exceptions -> file extraction -> AI Search incidents index.

    Returns summary counts with keys: fetched, prepared, pushed.
    """
    effective_fired_time = fired_time or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    exceptions = await fetch_exceptions(
        workspace_id=workspace_id,
        resource_name=resource_name,
        fired_time=effective_fired_time,
        window_minutes=window_minutes,
    )
    if not exceptions:
        return {"fetched": 0, "prepared": 0, "pushed": 0}

    prepared = 0
    pushed = 0
    for exception_data in exceptions:
        files = await extract_files(
            stacktrace=exception_data.get("stacktrace", ""),
            error_message=exception_data.get("message", ""),
        )
        if not files:
            continue

        exception_data["files_involved"] = sorted(set(files))
        incident = _build_incident_from_exception(exception_data)
        prepared += 1
        if push_incident(incident):
            pushed += 1

    return {"fetched": len(exceptions), "prepared": prepared, "pushed": pushed}


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


def main() -> None:
    """CLI for independent Azure-native log ingestion."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="PRism Azure-native incident ingest")
    parser.add_argument(
        "--workspace-id",
        default=os.getenv("AZURE_LOG_WORKSPACE_ID", ""),
        help="Azure Log Analytics workspace ID",
    )
    parser.add_argument(
        "--resource-name",
        default=os.getenv("AZURE_RESOURCE_NAME", ""),
        help="Service/resource name (cloud_RoleName)",
    )
    parser.add_argument(
        "--fired-time",
        default=os.getenv("AZURE_INGEST_FIRED_TIME", ""),
        help="Reference UTC time (ISO-8601), e.g. 2026-03-04T12:00:00Z",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=int(os.getenv("AZURE_INGEST_WINDOW_MINUTES", "30")),
        help="Query window in minutes around fired-time",
    )
    args = parser.parse_args()

    if not args.workspace_id:
        raise SystemExit("--workspace-id or AZURE_LOG_WORKSPACE_ID is required")
    if not args.resource_name:
        raise SystemExit("--resource-name or AZURE_RESOURCE_NAME is required")
    if not args.fired_time:
        raise SystemExit("--fired-time or AZURE_INGEST_FIRED_TIME is required")

    summary = asyncio.run(
        ingest_from_logs(
            workspace_id=args.workspace_id,
            resource_name=args.resource_name,
            fired_time=args.fired_time,
            window_minutes=args.window_minutes,
        )
    )
    logger.info(
        "Ingest complete: fetched=%d prepared=%d pushed=%d",
        summary["fetched"],
        summary["prepared"],
        summary["pushed"],
    )


if __name__ == "__main__":
    main()