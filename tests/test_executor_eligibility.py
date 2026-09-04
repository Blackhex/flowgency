from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from agency.configuration.store import ConfigStore
from agency.permissions.eligibility import may_execute_decisions


def _config(tmp_path: Path, raw_config, rules):
    raw = deepcopy(raw_config)
    raw["schema_version"] = 6
    workspace = raw["teams"]["newsletter"]["workspace_path"]
    raw["teams"]["newsletter"]["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [dict(r, path=r["path"].replace("<ws>", workspace)) for r in rules],
        }
    }
    # Use copilot so that restricted mode is accepted by the integration validator.
    raw["teams"]["newsletter"]["agents"][0]["integration"] = "copilot"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_write_on_the_workspace_confers_eligibility(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["read", "write"]}])

    assert may_execute_decisions(config, "newsletter", "builder") is True


def test_read_only_workspace_does_not(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["read"]}])

    assert may_execute_decisions(config, "newsletter", "builder") is False


def test_write_on_a_subdirectory_does_not(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        [
            {"path": "<ws>", "tools": ["read"]},
            {"path": "<ws>/scratch", "tools": ["read", "write"]},
        ],
    )

    assert may_execute_decisions(config, "newsletter", "builder") is False


def test_omitted_tools_confers_eligibility(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>"}])

    assert may_execute_decisions(config, "newsletter", "builder") is True


def test_unknown_group_is_not_eligible(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["write"]}])

    assert may_execute_decisions(config, "nope", "builder") is False


def test_unknown_agent_is_not_eligible(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["write"]}])

    assert may_execute_decisions(config, "newsletter", "nope") is False
