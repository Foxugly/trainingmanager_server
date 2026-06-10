"""Unit tests for the pure team.stats helpers, reachable directly now that they
live in team/stats.py (no longer private methods on TeamViewSet)."""

import datetime

import pytest
from rest_framework.exceptions import ValidationError

from team import stats


class _FakeRequest:
    """Minimal stand-in exposing the .query_params.get the helpers use."""

    def __init__(self, params):
        self.query_params = params


def test_parse_window_defaults_to_last_12_weeks():
    date_from, date_to = stats.parse_window(_FakeRequest({}))
    assert (date_to - date_from).days == 84


def test_parse_window_clamps_span_to_two_years():
    date_from, date_to = stats.parse_window(
        _FakeRequest({"from": "2000-01-01", "to": "2026-01-01"})
    )
    assert (date_to - date_from).days == stats.STATS_MAX_SPAN_DAYS


def test_parse_window_rejects_inverted_range():
    with pytest.raises(ValidationError):
        stats.parse_window(_FakeRequest({"from": "2026-05-01", "to": "2026-04-01"}))


def test_parse_window_rejects_malformed_date():
    with pytest.raises(ValidationError):
        stats.parse_window(_FakeRequest({"from": "not-a-date"}))


def test_bucket_by_week_groups_by_iso_monday():
    events = [
        {"date": datetime.date(2026, 5, 6), "total": 1000},  # Wed -> Mon 2026-05-04
        {"date": datetime.date(2026, 5, 8), "total": 500},  # Fri -> Mon 2026-05-04
        {"date": datetime.date(2026, 5, 11), "total": 800},  # Mon 2026-05-11
    ]
    buckets = stats.bucket_by_week(events)
    assert buckets == [
        {"week_start": datetime.date(2026, 5, 4), "distance": 1500},
        {"week_start": datetime.date(2026, 5, 11), "distance": 800},
    ]


def test_roti_stats_empty_on_team_aggregate():
    # member_id None => aggregate => empty ROTI series (ROTI is per athlete).
    assert stats.roti_stats([1, 2], [], None) == {"series": [], "average": None, "count": 0}
