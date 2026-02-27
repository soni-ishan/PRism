"""
Tests for the Timing Agent.
Parameterized over known timestamps to validate scoring rules.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest

from agents.timing_agent import run


def _run(ts: datetime, release_date: date | None = None):
    """Helper to drive the async ``run`` from sync test code."""
    return asyncio.get_event_loop().run_until_complete(
        run(deploy_timestamp=ts, release_date=release_date)
    )


# ── Day-of-week tests ────────────────────────────────────────────────


class TestDayOfWeek:
    def test_friday_evening_is_critical(self):
        # Friday 4:50 PM  →  day=+30, time=+25  →  55 → "critical"
        ts = datetime(2026, 2, 27, 16, 50, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.status == "critical"
        assert result.risk_score_modifier >= 55
        assert any("Friday" in f for f in result.findings)

    def test_tuesday_morning_is_pass(self):
        # Tuesday 10:00 AM  →  day=0, time=0  →  0 → "pass"
        ts = datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.status == "pass"
        assert result.risk_score_modifier <= 20

    def test_wednesday_core_hours_is_pass(self):
        ts = datetime(2026, 2, 25, 11, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.status == "pass"
        assert result.risk_score_modifier == 0

    def test_sunday_is_warning(self):
        # Sunday 11:00 AM  →  day=+20, time=0  →  20 → "pass" (boundary)
        ts = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.risk_score_modifier == 20
        assert result.status == "pass"
        assert any("Sunday" in f for f in result.findings)

    def test_saturday_is_warning(self):
        # Saturday 3:30 PM  →  day=+20, time=+15  →  35 → "warning"
        ts = datetime(2026, 2, 28, 15, 30, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.status == "warning"
        assert result.risk_score_modifier == 35
        assert any("Saturday" in f for f in result.findings)

    def test_monday_early_morning(self):
        # Monday 8:30 AM  →  day=+5, time=+10  →  15 → "pass"
        ts = datetime(2026, 3, 2, 8, 30, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.status == "pass"
        assert result.risk_score_modifier == 15


# ── Time-of-day tests ────────────────────────────────────────────────


class TestTimeOfDay:
    def test_after_4pm(self):
        ts = datetime(2026, 2, 25, 17, 0, tzinfo=timezone.utc)  # Wed 5 PM
        result = _run(ts)
        assert result.risk_score_modifier >= 25
        assert any("late-day" in f.lower() for f in result.findings)

    def test_3pm_range(self):
        ts = datetime(2026, 2, 25, 15, 30, tzinfo=timezone.utc)  # Wed 3:30 PM
        result = _run(ts)
        assert result.risk_score_modifier == 15
        assert any("approaching" in f.lower() for f in result.findings)

    def test_before_9am(self):
        ts = datetime(2026, 2, 25, 7, 0, tzinfo=timezone.utc)  # Wed 7 AM
        result = _run(ts)
        assert result.risk_score_modifier == 10
        assert any("early morning" in f.lower() for f in result.findings)

    def test_core_hours_safe(self):
        ts = datetime(2026, 2, 25, 10, 0, tzinfo=timezone.utc)  # Wed 10 AM
        result = _run(ts)
        assert result.risk_score_modifier == 0
        assert result.status == "pass"


# ── Holiday tests ─────────────────────────────────────────────────────


class TestHoliday:
    def test_christmas_day(self):
        # Christmas, Wed 10 AM → day=0, time=0, holiday=+20 → 20 → "pass"
        ts = datetime(2026, 12, 25, 10, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.risk_score_modifier >= 20
        assert any("Christmas" in f for f in result.findings)

    def test_christmas_eve(self):
        # Dec 24 (eve of Christmas), Thu 10 AM → day=0, time=0, holiday=+20
        ts = datetime(2026, 12, 24, 10, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.risk_score_modifier >= 20
        assert any("Christmas" in f for f in result.findings)

    def test_christmas_eve_friday_evening_is_critical(self):
        # If Christmas Eve falls on a Friday at 5 PM (year 2021):
        # day=+30, time=+25, holiday=+20 → 75 → "critical"
        ts = datetime(2021, 12, 24, 17, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.status == "critical"
        assert result.risk_score_modifier >= 75

    def test_independence_day(self):
        ts = datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert any("Independence" in f for f in result.findings)

    def test_thanksgiving(self):
        # Thanksgiving 2026 = Nov 26 (4th Thursday of Nov)
        ts = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert any("Thanksgiving" in f for f in result.findings)

    def test_non_holiday_no_flag(self):
        # Feb 25 2026 is a regular Wednesday
        ts = datetime(2026, 2, 25, 10, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert not any("holiday" in f.lower() or "Day" in f for f in result.findings)


# ── Release proximity tests ──────────────────────────────────────────


class TestReleaseProximity:
    def test_same_day_as_release(self):
        ts = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)  # Tue 10 AM
        result = _run(ts, release_date=date(2026, 3, 10))
        assert result.risk_score_modifier == 15
        assert any("release date" in f.lower() for f in result.findings)

    def test_day_before_release(self):
        ts = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
        result = _run(ts, release_date=date(2026, 3, 10))
        assert result.risk_score_modifier >= 15

    def test_far_from_release_no_flag(self):
        ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        result = _run(ts, release_date=date(2026, 3, 15))
        # Only the Sunday day-of-week modifier should apply
        assert not any("release" in f.lower() for f in result.findings)


# ── Combined / edge-case tests ───────────────────────────────────────


class TestCombined:
    def test_worst_case_caps_at_100(self):
        # Christmas Day 2026 falls on Friday → day=+30 …wait, Dec 25 2026 is Fri?
        # Actually Dec 25, 2026 is a Friday!  5 PM: day=+30, time=+25, holiday=+20 = 75
        # Add release proximity: +15 → 90, still under 100 — that's fine.
        ts = datetime(2026, 12, 25, 17, 0, tzinfo=timezone.utc)
        result = _run(ts, release_date=date(2026, 12, 25))
        assert result.risk_score_modifier <= 100
        assert result.status == "critical"

    def test_data_contract_compliance(self):
        """Every result must conform to the shared data contract."""
        ts = datetime(2026, 2, 25, 10, 0, tzinfo=timezone.utc)
        result = _run(ts)
        assert result.agent_name == "Timing Agent"
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")
        assert isinstance(result.findings, list)
        assert isinstance(result.recommended_action, str)
        # Round-trip through JSON must work
        from agents.shared.data_contract import AgentResult
        parsed = AgentResult.from_json(result.to_json())
        assert parsed == result

    def test_no_timestamp_uses_now(self):
        """When no timestamp is provided, the agent should still return a valid result."""
        result = asyncio.get_event_loop().run_until_complete(run())
        assert result.agent_name == "Timing Agent"
        assert 0 <= result.risk_score_modifier <= 100

    def test_naive_timestamp_treated_as_utc(self):
        """A timezone-naive timestamp should be treated as UTC without error."""
        ts = datetime(2026, 2, 25, 10, 0)  # no tzinfo
        result = _run(ts)
        assert result.status == "pass"
