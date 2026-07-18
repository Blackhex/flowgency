from __future__ import annotations

import ast
from pathlib import Path


APP_PATH = Path(__file__).parents[1] / "agency" / "app.py"


def test_app_defines_no_parallel_config_authority() -> None:
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    forbidden_names = {
        "CONFIG",
        "GROUPS",
        "load_config",
        "save_config",
        "reload_groups",
        "normalize_agents",
        "agent_names",
    }

    definitions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    name_uses = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }

    assert not (definitions | name_uses) & forbidden_names
