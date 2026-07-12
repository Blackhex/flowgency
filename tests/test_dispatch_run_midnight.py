"""Test for deterministic midnight-boundary at rule behavior."""
import pytest
from datetime import datetime
from pathlib import Path
from agency.dispatch.run import run_dispatch_cycle


def test_repeated_heartbeat_with_fixed_time_does_not_duplicate_daily_at_rule(tmp_path, monkeypatch):
    """Prove at rules use consistent date when checking markers even across repeated cycles.
    
    Uses fixed datetime to prevent rare midnight-crossing flakes.
    """
    # Set up group structure
    group_path = tmp_path / "grp"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CLAUDE.md").write_text("# Product\n")
    prompts = group_path / "shared" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "routine.md").write_text("do the thing")
    log_dir = group_path / "shared" / "logs"
    log_dir.mkdir(parents=True)
    
    # Fixed time: 2026-07-12 09:15:00 (within window of 09:00 at rule)
    fixed_dt = datetime(2026, 7, 12, 9, 15, 0)
    
    # Monkeypatch datetime.now() and datetime.fromtimestamp() in dispatch.run module
    class MockDatetime:
        @staticmethod
        def now():
            return fixed_dt
        
        @staticmethod
        def fromtimestamp(ts):
            # For the check_at_rule now_epoch parameter
            return datetime.fromtimestamp(ts)
        
        @staticmethod
        def strptime(date_string, format):
            return datetime.strptime(date_string, format)
    
    # Patch at module level
    monkeypatch.setattr("agency.dispatch.run.datetime", MockDatetime)
    
    config = {
        "agency": {"dispatch": {"interval": 15}},
        "groups": {
            "test": {
                "path": str(group_path),
                "agents": ["product"],
                "dispatch": {
                    "enabled": True,
                    "agents": {"product": [{"prompt": "routine.md", "at": "09:00"}]},
                },
            }
        },
    }
    
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job",
        lambda spec, launcher=None: submitted.append(spec),
    )
    
    # Run two cycles with the same fixed time
    run_dispatch_cycle(config, tmp_path / "config.yaml")
    run_dispatch_cycle(config, tmp_path / "config.yaml")
    
    # Must submit only once
    assert len(submitted) == 1
    
    # Verify the event marker was created in the correct date subdirectory
    event_marker = log_dir / "2026-07-12" / ".event-product-routine"
    assert event_marker.exists()
