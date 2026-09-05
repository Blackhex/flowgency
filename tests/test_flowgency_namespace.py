from __future__ import annotations

import importlib
import json
from pathlib import Path
import pytest
import tomllib


REPO_ROOT = Path(__file__).parents[1]


def test_python_distribution_and_import_namespace_are_flowgency():
    project = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["name"] == "flowgency"
    assert project["project"]["scripts"] == {
        "flowgency": "flowgency.cli:main",
    }
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "flowgency*"
    ]
    assert (REPO_ROOT / "flowgency").is_dir()
    importlib.import_module("flowgency")
    importlib.import_module("flowgency.integrations.flowgency")

    previous_package = "".join(("a", "gency"))
    assert not (REPO_ROOT / previous_package).exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(previous_package)


def test_ui_test_package_uses_flowgency_name():
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(
        (REPO_ROOT / "package-lock.json").read_text(encoding="utf-8")
    )
    assert package["name"] == "flowgency-ui-gate"
    assert package_lock["name"] == "flowgency-ui-gate"
    assert package_lock["packages"][""]["name"] == "flowgency-ui-gate"
