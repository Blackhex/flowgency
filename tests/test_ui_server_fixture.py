"""Focused test: UI fixture server must set up runtime without TypeError."""
import sys
from pathlib import Path

import yaml

_TESTS_DIR = Path(__file__).resolve().parent


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    _marker, payload, _body = text.split("---\n", 2)
    return yaml.safe_load(payload)


def test_prepare_runtime_creates_deterministic_fixture_without_type_error():
    """_prepare_runtime() must not raise TypeError from renamed selector keyword.

    RED proof: before the fix, _seed_memory() passes team_key= to
    resolve_memory_selector() which now expects team_key=.
    """
    if str(_TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(_TESTS_DIR))
    from ui.server import _prepare_runtime, _safe_remove_runtime

    runtime, config_path = _prepare_runtime()
    try:
        assert runtime.is_dir(), "runtime directory must be created"
        assert config_path.is_file(), "config file must be written"
    finally:
        _safe_remove_runtime(runtime)


def test_prepare_runtime_seeds_real_ticket_numbers_and_active_runs():
    if str(_TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(_TESTS_DIR))
    from ui.server import _prepare_runtime, _safe_remove_runtime

    runtime, _config_path = _prepare_runtime()
    try:
        delivery_root = runtime / "tickets" / "delivery" / "newsletter" / "delivery" / "tickets"
        research_root = runtime / "tickets" / "research" / "newsletter" / "research-workflow" / "tickets"

        delivery_records = {
            path.stem: _front_matter(path)
            for path in delivery_root.glob("*.md")
        }
        research_records = {
            path.stem: _front_matter(path)
            for path in research_root.glob("*.md")
        }

        assert sorted(record["number"] for record in delivery_records.values()) == list(range(101, 109))
        assert sorted(record["number"] for record in research_records.values()) == [201, 202, 203, 204]
        assert delivery_records["fixture-review"]["assignee"] == "reviewer"
        assert delivery_records["fixture-review-2"]["assignee"] is None

        active_ticket_ids = sorted(
            ticket_id
            for ticket_id, record in delivery_records.items()
            if record["active_run"] is not None
        )
        assert active_ticket_ids == ["fixture-active-1", "fixture-active-2"]
        assert {delivery_records[ticket_id]["active_run"]["job_id"] for ticket_id in active_ticket_ids} == {"fixture-active-job"}
    finally:
        _safe_remove_runtime(runtime)
