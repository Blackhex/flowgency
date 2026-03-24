"""Tests for mission control dashboard helpers."""
from datetime import datetime, timedelta
from agency.app import build_pipeline_stats, build_activity_feed


class TestBuildPipelineStats:
    def test_counts_items_per_stage(self):
        observations = [{"status": "open"}, {"status": "open"}, {"status": "archived"}]
        proposals = [{"status": "proposed"}]
        decisions = [{"answers": {"approve": "approved"}}, {"answers": {"approach": "Option A"}}]
        result = build_pipeline_stats(observations, proposals, decisions)
        assert result["observations"]["total"] == 3
        assert result["proposals"]["total"] == 1
        assert result["decisions"]["total"] == 2

    def test_sparkline_buckets_last_7_days(self):
        today = datetime.now()
        observations = [{"date": (today - timedelta(days=i)).isoformat()} for i in range(3)]
        result = build_pipeline_stats(observations, [], [])
        assert len(result["observations"]["sparkline"]) == 7

    def test_empty_pipeline(self):
        result = build_pipeline_stats([], [], [])
        assert result["observations"]["total"] == 0
        assert result["proposals"]["total"] == 0
        assert result["decisions"]["total"] == 0
        assert len(result["observations"]["sparkline"]) == 7

    def test_detects_bottleneck(self):
        observations = [{"status": "open"}] * 10
        proposals = [{"status": "proposed"}]
        decisions = []
        result = build_pipeline_stats(observations, proposals, decisions)
        assert result["flow_status"] == "bottleneck"

    def test_healthy_flow(self):
        observations = [{"status": "open"}] * 3
        proposals = [{"status": "proposed"}] * 2
        decisions = [{"answers": {"approve": "approved"}}] * 2
        result = build_pipeline_stats(observations, proposals, decisions)
        assert result["flow_status"] == "healthy"


class TestBuildActivityFeed:
    def test_interleaves_observations_and_proposals(self):
        obs = [
            {"agent": "scout", "_slug": "obs-1", "date": "2026-03-22T10:00:00", "status": "open"},
            {"agent": "scout", "_slug": "obs-2", "date": "2026-03-22T08:00:00", "status": "open"},
        ]
        props = [
            {"origin_agent": "arch", "_slug": "prop-1", "date": "2026-03-22T09:00:00", "status": "proposed"},
        ]
        feed = build_activity_feed(obs, props, limit=10)
        assert len(feed) == 3
        assert feed[0]["slug"] == "obs-1"
        assert feed[1]["slug"] == "prop-1"
        assert feed[2]["slug"] == "obs-2"

    def test_limits_results(self):
        obs = [{"agent": f"a{i}", "_slug": f"obs-{i}", "date": f"2026-03-{20+i}T10:00:00", "status": "open"} for i in range(10)]
        feed = build_activity_feed(obs, [], limit=5)
        assert len(feed) == 5

    def test_handles_empty_input(self):
        feed = build_activity_feed([], [])
        assert feed == []
