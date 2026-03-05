"""
History Agent - Correlates PR changes with past incidents to assess deployment risk.
Data Source: Azure AI Search via MCP Server
"""
import json
import sys
import os

# Add project root to path for imports
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from datetime import datetime, timezone
from typing import Any, Dict, List

from agents.shared.data_contract import AgentResult
from mcp_servers.azure_mcp_server.mcp_server import AzureMCPServer


class HistoryAgent:
    """
    Correlates PR changes with past incidents to assess deployment risk.
    
    Data Source: Azure AI Search (incidents index)
    
    File Matching: STRICT - Exact path/basename matching only.
    Two files with similar names in different locations are treated as separate.
    
    Data Contract Output:
    {
      "agent_name": "History Agent",
      "risk_score_modifier": 0-100,
      "status": "pass|warning|critical",
      "findings": ["finding1", "finding2"],
      "recommended_action": "Clear recommendation"
    }
    """
    
    def __init__(self, azure_mcp: AzureMCPServer = None):
        """
        Initialize the agent with Azure MCP server connection.
        
        Args:
            azure_mcp: Optional MCP server instance. If None, creates a new one.
            
        Raises:
            RuntimeError: If Azure connection fails
        """
        self.incidents = []
        self.deployment_events = []
        
        # Connect to Azure (or use provided connection)
        try:
            self.azure_mcp = azure_mcp or AzureMCPServer()
            print("[HistoryAgent] ✅ Connected to Azure AI Search", file=sys.stderr)
        except Exception as e:
            print(f"[HistoryAgent] ❌ Azure connection failed: {e}", file=sys.stderr)
            print("[HistoryAgent] 💡 Run: python setup_azure_search.py", file=sys.stderr)
            raise RuntimeError(f"Azure AI Search required: {e}") from e
    
    def analyze_pr(self, pr_files: List[str]) -> Dict[str, Any]:
        """
        Analyze a PR by correlating changed files with past incidents.
        
        Args:
            pr_files: List of file paths changed in the PR (e.g., ["payment_service.py"])
        
        Returns:
            JSON contract conforming to Verdict Agent expectations
        """
        if not pr_files:
            return self._build_response(0, "pass", [], "No files changed — minimal risk.")
        
        findings = []
        risk_score = 0
        
        # ===== STEP 0: Fetch incidents from Azure or mock data =====
        self._fetch_incidents_from_azure(pr_files)
        
        # ===== STEP 1: Correlate files with incidents =====
        file_incident_map = self._correlate_files_with_incidents(pr_files)
        
        for file_path, incident_list in file_incident_map.items():
            if incident_list:
                count = len(incident_list)
                pct = (count / len(self.incidents)) * 100 if self.incidents else 0
                finding = f"{file_path} involved in {count} incident(s) ({pct:.0f}% of all incidents)"
                findings.append(finding)
                
                # Calculate risk: files involved in many incidents = higher risk
                file_risk = min(50, count * 10)  # Max 50 points per file
                risk_score += file_risk
                
                # Add specific incident details (most recent 2 by timestamp)
                recent_incidents = sorted(
                    incident_list,
                    key=self._incident_timestamp_sort_key,
                    reverse=True,
                )
                for incident in recent_incidents[:2]:
                    detail = f"  └─ {incident['timestamp'][:10]}: {incident['title']} ({incident['severity']})"
                    findings.append(detail)
        
        # ===== STEP 2: Check deployment frequency =====
        deploy_risk, deploy_finding = self._check_deployment_frequency(pr_files)
        if deploy_finding:
            findings.append(deploy_finding)
            risk_score += deploy_risk
        
        # ===== STEP 3: Determine status and action =====
        if risk_score >= 70:
            status = "critical"
            recommended_action = f"BLOCK DEPLOYMENT. High historical risk detected. Trigger Coverage Agent to validate tests on {pr_files[0]}."
        elif risk_score >= 40:
            status = "warning"
            recommended_action = "CAUTION: This file has incident history. Require extended test validation and peer review."
        else:
            status = "pass"
            recommended_action = "No significant incident history. Safe to proceed."
        
        return self._build_response(risk_score, status, findings, recommended_action)
    
    def _fetch_incidents_from_azure(self, pr_files: List[str]) -> None:
        """
        Fetch incidents from Azure AI Search for the given files.
        
        Azure AI Search VALUE (even with strict local matching):
        ✓ Searches across multiple fields: files_involved, title, error_message, root_cause
        ✓ Returns incidents where file is mentioned in descriptions (not just files_involved)
        ✓ Efficient full-text search with ranking by relevance
        ✓ Can find related incidents even if exact path differs slightly
        
        We then apply STRICT local filtering to ensure precision.
        
        Args:
            pr_files: List of file paths to search for
            
        Raises:
            RuntimeError: If Azure query fails
        """
        try:
            print(f"[HistoryAgent] 🔍 Searching incidents for: {pr_files}", file=sys.stderr)
            
            self.incidents = self.azure_mcp.query_incidents_by_files_search(
                file_paths=pr_files,
                top_k=50
            )
            
            count = len(self.incidents)
            print(f"[HistoryAgent] ✅ Found {count} incident(s) from Azure", file=sys.stderr)
                
        except Exception as e:
            print(f"[HistoryAgent] ❌ Query failed: {e}", file=sys.stderr)
            raise RuntimeError(f"Failed to fetch incidents from Azure: {e}") from e
    
    def _correlate_files_with_incidents(self, pr_files: List[str]) -> Dict[str, List[Dict]]:
        """
        Find all incidents that involved any of the PR's changed files.
        Uses STRICT exact matching on paths/filenames.
        
        Azure AI Search returns incidents where the file name appears in:
        - files_involved array (exact or partial text match)
        - incident title, error message, or root cause (semantic search)
        
        We then filter to only exact path/basename/stem matches.
        
        Returns:
            Map of file_path -> list of incidents
        """
        file_incident_map = {f: [] for f in pr_files}

        # Normalize PR files for matching
        normalized_pr_files = {
            pr_file: self._normalize_file_key(pr_file) for pr_file in pr_files
        }
        
        for incident in self.incidents:
            incident_files = incident.get("files_involved", [])
            normalized_incident_files = [
                self._normalize_file_key(path)
                for path in incident_files
                if isinstance(path, str)
            ]

            for pr_file in pr_files:
                pr_key = normalized_pr_files[pr_file]
                if any(self._file_keys_match(pr_key, incident_key) for incident_key in normalized_incident_files):
                    file_incident_map[pr_file].append(incident)
        
        return file_incident_map

    def _normalize_file_key(self, file_path: str) -> tuple[str, str, str]:
        """
        Build a normalized file identity tuple for strict matching:
          (normalized_full_path, basename, stem)

        STRICT matching prevents false positives:
        - api/user.py ≠ models/user.py (different modules)
        - payments.py ≠ payment_service.py (different files)
        """
        normalized = str(file_path or "").strip().replace("\\", "/").lower()
        normalized = normalized.lstrip("./")

        basename = normalized.rsplit("/", 1)[-1] if normalized else ""
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename

        return normalized, basename, stem

    def _file_keys_match(
        self,
        pr_file_key: tuple[str, str, str],
        incident_file_key: tuple[str, str, str],
    ) -> bool:
        """
        Match files using STRICT exact matching:
        1. Exact full path match (e.g., "src/api/payment.py")
        2. Exact basename match (e.g., "payment_service.py")
        3. Exact stem match (e.g., "payment_service")
        
        NO fuzzy matching to avoid false positives:
        - "payments.py" will NOT match "payment_service.py"
        - "api/user.py" will NOT match "models/user.py"
        """
        pr_path, pr_basename, pr_stem = pr_file_key
        incident_path, incident_basename, incident_stem = incident_file_key

        # Only exact matches
        return (
            pr_path == incident_path or 
            pr_basename == incident_basename or 
            pr_stem == incident_stem
        )

    def _incident_timestamp_sort_key(self, incident: Dict[str, Any]) -> datetime:
        """Parse incident timestamp for deterministic recent-first sorting."""
        raw_timestamp = str(incident.get("timestamp", "")).strip()
        if not raw_timestamp:
            return datetime.min.replace(tzinfo=timezone.utc)

        normalized_timestamp = raw_timestamp.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized_timestamp)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    
    def _check_deployment_frequency(self, pr_files: List[str]) -> tuple:
        """
        Check how many times files have been deployed today and recently.
        If deployed multiple times today, risk increases (Friday effect, etc.).
        
        Note: Requires deployment event data. Returns (0, "") if no deployment data available.
        
        Returns:
            (risk_score, finding_string)
        """
        if not self.deployment_events:
            # No deployment data available (not yet implemented in Azure AI Search)
            return 0, ""
        
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Count deployments of these files TODAY
        deploys_today = 0
        for deploy in self.deployment_events:
            deploy_time = datetime.fromisoformat(deploy["timestamp"].replace("Z", "+00:00"))
            if deploy_time >= today_start and deploy_time <= now:
                if any(f in deploy.get("files_changed", []) for f in pr_files):
                    deploys_today += 1
        
        risk = 0
        finding = ""
        
        if deploys_today >= 3:
            risk = 30
            finding = f"⚠️  DEPLOYMENT FREQUENCY: {deploys_today} deployments of these files already today. Fatigue risk."
        elif deploys_today >= 1:
            risk = 15
            finding = f"ℹ️  {deploys_today} deployment(s) of these files today. Monitor closely."
        
        return risk, finding
    
    def _build_response(self, risk_score: int, status: str, findings: List[str], action: str) -> Dict[str, Any]:
        """Build the standard response JSON contract."""
        # Clamp risk score to 0-100
        risk_score = max(0, min(100, risk_score))
        
        return {
            "agent_name": "History Agent",
            "risk_score_modifier": risk_score,
            "status": status,
            "findings": findings if findings else ["No relevant incident history found."],
            "recommended_action": action
        }


def main():
    """
    CLI entry point for testing the History Agent.
    
    Usage:
        python agents/history_agent/agent.py <file1> <file2> ...
    
    Prerequisites:
        python setup_azure_search.py
    """
    files_changed = sys.argv[1:] if len(sys.argv) > 1 else ["payment_service.py"]
    
    if len(sys.argv) < 2:
        print("[HistoryAgent] No files provided, using test: payment_service.py", file=sys.stderr)
    
    try:
        agent = HistoryAgent()
        result = agent.analyze_pr(files_changed)
        print(json.dumps(result, indent=2))
        
    except RuntimeError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        print("\n📝 Setup: python setup_azure_search.py", file=sys.stderr)
        sys.exit(1)


async def run(changed_files: list[str]) -> AgentResult:
    """PRism orchestrator interface."""
    agent = HistoryAgent()
    result = agent.analyze_pr(changed_files)
    return AgentResult.model_validate(result)


if __name__ == "__main__":
    main()