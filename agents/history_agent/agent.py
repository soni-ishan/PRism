import json
import sys
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from agents.shared.data_contract import AgentResult

class HistoryAgent:
    """
    Correlates PR changes with past incidents to assess deployment risk.
    
    Data Contract Output:
    {
      "agent_name": "History Agent",
      "risk_score_modifier": 0-100,
      "status": "pass|warning|critical",
      "findings": ["finding1", "finding2"],
      "recommended_action": "Clear recommendation"
    }
    """
    
    def __init__(self, mock_data_path: str = None):
        """Initialize the agent and load mock incident data."""
        # If no path provided, resolve it relative to this file's location
        if mock_data_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            mock_data_path = os.path.join(current_dir, "mock_incidents.json")
        
        self.mock_data_path = mock_data_path
        self.incidents = []
        self.deployment_events = []
        self.load_mock_data()
    
    def load_mock_data(self) -> None:
        """Load mock incident and deployment data from JSON file."""
        try:
            with open(self.mock_data_path, 'r') as f:
                data = json.load(f)
                self.incidents = data.get("incidents", [])
                self.deployment_events = data.get("deployment_events", [])
            print(f"[HistoryAgent] ✅ Loaded {len(self.incidents)} incidents and {len(self.deployment_events)} deployments", file=sys.stderr)
        except FileNotFoundError:
            print(f"[HistoryAgent] ❌ ERROR: Mock data file not found at {self.mock_data_path}", file=sys.stderr)
            print(f"[HistoryAgent] Expected location: {os.path.abspath(self.mock_data_path)}", file=sys.stderr)
            self.incidents = []
            self.deployment_events = []
    
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
    
    def _correlate_files_with_incidents(self, pr_files: List[str]) -> Dict[str, List[Dict]]:
        """
        Find all incidents that involved any of the PR's changed files.
        
        Returns:
            Map of file_path -> list of incidents
        """
        file_incident_map = {f: [] for f in pr_files}

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
        Build a normalized file identity tuple:
          (normalized_full_path, basename, stem)

        Matching on this tuple avoids substring false positives like:
        user.py matching superuser.py.
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
        """Match by exact normalized path, basename, or stem."""
        pr_path, pr_basename, pr_stem = pr_file_key
        incident_path, incident_basename, incident_stem = incident_file_key

        return (
            pr_path == incident_path
            or pr_basename == incident_basename
            or pr_stem == incident_stem
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
        
        Returns:
            (risk_score, finding_string)
        """
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
    Entry point for testing the History Agent.
    Usage: python history_agent.py <file1> <file2> ...
    """
    if len(sys.argv) < 2:
        # Default test case
        files_changed = ["payment_service.py"]
        print("[HistoryAgent] No files provided, using test case: payment_service.py", file=sys.stderr)
    else:
        files_changed = sys.argv[1:]
    
    agent = HistoryAgent()
    result = agent.analyze_pr(files_changed)
    
    # Output as JSON (stdout)
    print(json.dumps(result, indent=2))


async def run(changed_files: list[str]) -> AgentResult:
    """PRism agent interface entrypoint for History Agent."""
    agent = HistoryAgent()
    result = agent.analyze_pr(changed_files)
    return AgentResult.model_validate(result)


if __name__ == "__main__":
    main()