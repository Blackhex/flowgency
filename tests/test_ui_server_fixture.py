"""Focused test: UI fixture server must set up runtime without TypeError."""
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent


def test_prepare_runtime_creates_deterministic_fixture_without_type_error():
    """_prepare_runtime() must not raise TypeError from renamed selector keyword.

    RED proof: before the fix, _seed_memory() passes group_key= to
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
