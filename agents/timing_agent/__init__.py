"""
PRism Timing Agent
==================
Evaluates deployment timing risk based on day-of-week, time-of-day,
holiday proximity, and release proximity.

No external API calls — pure datetime logic + risk lookup tables.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from agents.shared.data_contract import AgentResult

# ── Major US Federal Holidays (month, day) ───────────────────────────
# Fixed-date holidays.  Floating holidays are computed dynamically.

_FIXED_HOLIDAYS: dict[str, tuple[int, int]] = {
    "New Year's Day": (1, 1),
    "Juneteenth": (6, 19),
    "Independence Day": (7, 4),
    "Veterans Day": (11, 11),
    "Christmas Day": (12, 25),
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the *n*-th occurrence of *weekday* (0=Mon) in *month*/*year*."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first.replace(day=1 + offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of *weekday* in *month*/*year*."""
    candidate = _nth_weekday(year, month, weekday, 4)
    try:
        fifth = candidate.replace(day=candidate.day + 7)
        if fifth.month == month:
            return fifth
    except ValueError:
        pass
    return candidate


def _get_federal_holidays(year: int) -> dict[date, str]:
    """Return a mapping of date → holiday name for the given *year*."""
    holidays: dict[date, str] = {}

    for name, (m, d) in _FIXED_HOLIDAYS.items():
        holidays[date(year, m, d)] = name

    holidays[_nth_weekday(year, 1, 0, 3)] = "MLK Day"
    holidays[_nth_weekday(year, 2, 0, 3)] = "Presidents' Day"
    holidays[_last_weekday(year, 5, 0)] = "Memorial Day"
    holidays[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    holidays[_nth_weekday(year, 10, 0, 2)] = "Columbus Day"
    holidays[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving"

    return holidays


def _is_holiday_or_eve(d: date) -> tuple[bool, str | None]:
    """Check if *d* is a US federal holiday or the day before one."""
    holidays = _get_federal_holidays(d.year)

    if d in holidays:
        return True, f"Deploy date is {holidays[d]}"

    tomorrow = d + timedelta(days=1)
    holidays_next = (
        _get_federal_holidays(tomorrow.year)
        if tomorrow.year != d.year
        else holidays
    )
    if tomorrow in holidays_next:
        return True, f"Deploy date is the eve of {holidays_next[tomorrow]}"

    return False, None


# ── Risk Scoring Dimensions ──────────────────────────────────────────

def _day_of_week_risk(dt: datetime) -> tuple[int, str | None]:
    weekday = dt.weekday()  # 0=Mon … 6=Sun
    if weekday == 4:  # Friday
        return 30, "Deployment on Friday — historically high incident rate"
    if weekday in (5, 6):
        day_name = "Saturday" if weekday == 5 else "Sunday"
        return 20, f"Deployment on {day_name} — reduced on-call staffing"
    if weekday == 0:  # Monday
        return 5, "Deployment on Monday — slightly elevated start-of-week risk"
    return 0, None  # Tue–Thu


def _time_of_day_risk(dt: datetime) -> tuple[int, str | None]:
    t = dt.time()
    pretty = t.strftime("%I:%M %p").lstrip("0")
    if t >= time(16, 0):
        return 25, f"Deployment at {pretty} — late-day deploys correlate with incidents"
    if t >= time(15, 0):
        return 15, f"Deployment at {pretty} — approaching end of business day"
    if t < time(9, 0):
        return 10, f"Deployment at {pretty} — early morning, limited team availability"
    return 0, None  # Core hours 9 AM – 3 PM


def _holiday_risk(dt: datetime) -> tuple[int, str | None]:
    is_near, description = _is_holiday_or_eve(dt.date())
    if is_near:
        return 20, description
    return 0, None


def _release_proximity_risk(
    dt: datetime, release_date: date | None
) -> tuple[int, str | None]:
    if release_date is None:
        return 0, None
    delta = abs((dt.date() - release_date).days)
    if delta <= 1:
        return 15, f"Deployment on or within 1 day of release date ({release_date.isoformat()})"
    return 0, None


# ── Public API ────────────────────────────────────────────────────────

async def run(
    deploy_timestamp: datetime | None = None,
    release_date: date | None = None,
) -> AgentResult:
    """Evaluate deployment timing risk.

    Args:
        deploy_timestamp: When the deployment is planned.  Defaults to now (UTC).
        release_date:     Optional upcoming release date to check proximity.

    Returns:
        AgentResult with the timing risk assessment.
    """
    if deploy_timestamp is None:
        deploy_timestamp = datetime.now(timezone.utc)

    # Ensure timezone-aware — preserve original timezone so time-of-day risk
    # and display reflect the deployer's local time, not server UTC.
    if deploy_timestamp.tzinfo is None:
        deploy_timestamp = deploy_timestamp.replace(tzinfo=timezone.utc)

    findings: list[str] = []
    total_modifier = 0

    for scorer in [
        lambda: _day_of_week_risk(deploy_timestamp),
        lambda: _time_of_day_risk(deploy_timestamp),
        lambda: _holiday_risk(deploy_timestamp),
        lambda: _release_proximity_risk(deploy_timestamp, release_date),
    ]:
        modifier, finding = scorer()
        total_modifier += modifier
        if finding:
            findings.append(finding)

    total_modifier = min(total_modifier, 100)

    # Map to status
    if total_modifier <= 20:
        status = "pass"
    elif total_modifier <= 50:
        status = "warning"
    else:
        status = "critical"

    # Build recommendation
    if status == "pass":
        recommended_action = "Deploy window is safe — proceed with deployment."
    elif status == "critical":
        recommended_action = (
            "Delay deployment to the next safe window "
            "(Tuesday–Thursday, 9 AM – before 3 PM)."
        )
    else:
        recommended_action = (
            "Consider delaying deployment to a lower-risk window if possible."
        )

    if not findings:
        findings.append(
            "Deploy window is within core business hours on a low-risk day — safe."
        )

    return AgentResult(
        agent_name="Timing Agent",
        risk_score_modifier=total_modifier,
        status=status,
        findings=findings,
        recommended_action=recommended_action,
    )
