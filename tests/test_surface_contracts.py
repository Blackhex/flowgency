from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod


REPO_ROOT = Path(__file__).parents[1]
RETIRED_ROUTES = {
    ("GET", "/{group}/documents"),
    ("GET", "/{group}/documents/view"),
    ("POST", "/{group}/documents/save"),
    ("GET", "/{group}/prompts"),
    ("GET", "/{group}/prompts/{slug:promptslug}"),
    ("POST", "/{group}/prompts/{slug:promptslug}/save"),
    ("POST", "/{group}/prompts/dispatch"),
    ("GET", "/{group}/memory"),
    ("GET", "/{group}/memory/view"),
    ("POST", "/{group}/memory/save"),
    ("POST", "/{group}/agents/{agent}/identity"),
    ("POST", "/{group}/agents/{agent}/definition"),
    ("POST", "/{group}/agents/{agent}/upload-headshot"),
    ("GET", "/{group}/agents/{agent}/headshot"),
    ("POST", "/{group}/agents/{agent}/toggle-subagent"),
    ("POST", "/admin/orgs/{org}/initialize"),
    ("POST", "/admin/orgs/{org}/autodetect"),
}
RETIRED_TEMPLATES = {
    "admin_agent_detail.html",
    "agent_profile.html",
    "prompts.html",
    "prompt_detail.html",
    "memory.html",
    "memory_view.html",
    "documents.html",
    "document_view.html",
}


def _snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _active_documentation_paths() -> list[Path]:
    return [
        REPO_ROOT / "README.md",
        REPO_ROOT / "CLAUDE.md",
        *sorted((REPO_ROOT / "kb").glob("*.md")),
        *sorted((REPO_ROOT / "skills" / "agency-setup").rglob("*.md")),
    ]


def _without_superseded_migration_sections(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    kept: list[str] = []
    skipped_level: int | None = None
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).lower()
            if skipped_level is not None and level <= skipped_level:
                skipped_level = None
            if "migration" in title or "v1 history" in title:
                skipped_level = level
                continue
        if skipped_level is None:
            kept.append(line)
    return "\n".join(kept)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


def _superseded_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    library = tmp_path / "agent-library"
    blueprint = library / "advisor"
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_text("# Advisor\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review current work.\n---\n\nReview it.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.md").write_text("unchanged\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "agency": {
                    "title": "Agency",
                    "default_group": "newsletter",
                    "ai_backend": "claude-code",
                    "agent_library": str(library),
                    "compilation_cache": str(tmp_path / "cache"),
                    "memory_store": str(tmp_path / "memory-store"),
                },
                "memory": {"channels": {}},
                "groups": {
                    "newsletter": {
                        "name": "Newsletter",
                        "path": str(workspace),
                        "default_integration": "claude-code",
                        "agents": [
                            {
                                "name": "advisor",
                                "blueprint": "advisor",
                                "integration": "claude-code",
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), tmp_path


def test_removed_conversion_surfaces_do_not_exist(repo_root: Path):
    removed = [
        repo_root / "agency" / "configuration" / "compat.py",
        repo_root / "tools" / "migrate_agent_model.py",
        repo_root / "skills" / "agency-migration",
        repo_root / ".github" / "skills" / "agency-migration",
    ]
    assert not any(path.exists() for path in removed)


def test_retired_routes_are_not_registered():
    registered = set()
    for route in app_mod.app.routes:
        for method in getattr(route, "methods", set()):
            registered.add((method, route.path))
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if not callable(effective_route_contexts):
            continue
        for route_context in effective_route_contexts():
            starlette_route = getattr(route_context, "starlette_route", None)
            original_route = getattr(route_context, "original_route", None)
            methods = getattr(starlette_route, "methods", None) or getattr(
                original_route,
                "methods",
                set(),
            )
            path = getattr(starlette_route, "path", "") or getattr(
                original_route,
                "path",
                "",
            )
            for method in methods:
                registered.add((method, path))
    assert RETIRED_ROUTES.isdisjoint(registered)


def test_retired_routes_return_ordinary_404_without_mutating_source(tmp_path, monkeypatch):
    client, source_root = _superseded_client(tmp_path, monkeypatch)
    before = _snapshot_bytes(source_root)
    requests = [
        ("get", "/newsletter/documents", {}),
        ("get", "/newsletter/documents/view?path=source.md", {}),
        ("post", "/newsletter/documents/save", {"data": {"path": "source.md", "content": "changed"}}),
        ("get", "/newsletter/prompts", {}),
        ("get", "/newsletter/prompts/daily-review", {}),
        ("post", "/newsletter/prompts/daily-review/save", {"data": {"content": "changed"}}),
        ("post", "/newsletter/prompts/dispatch", {"data": {}}),
        ("get", "/newsletter/memory", {}),
        ("get", "/newsletter/memory/view?path=source.md", {}),
        ("post", "/newsletter/memory/save", {"data": {"path": "source.md", "content": "changed"}}),
        ("post", "/newsletter/agents/advisor/identity", {"data": {"display_name": "Changed"}}),
        ("post", "/newsletter/agents/advisor/definition", {"data": {"body": "Changed"}}),
        ("post", "/newsletter/agents/advisor/upload-headshot", {"files": {"file": ("headshot.png", b"changed", "image/png")}}),
        ("get", "/newsletter/agents/advisor/headshot", {}),
        ("post", "/newsletter/agents/advisor/toggle-subagent", {"data": {}}),
        ("post", "/admin/orgs/newsletter/initialize", {"data": {}}),
        ("post", "/admin/orgs/newsletter/autodetect", {"data": {}}),
    ]

    for method, path, kwargs in requests:
        response = getattr(client, method)(path, follow_redirects=False, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)

    assert _snapshot_bytes(source_root) == before


def test_retired_templates_are_deleted_and_navigation_uses_canonical_surfaces():
    template_root = REPO_ROOT / "agency" / "templates"
    assert not {path.name for path in template_root.iterdir()} & RETIRED_TEMPLATES
    navigation = (template_root / "base.html").read_text(encoding="utf-8")
    for retired_href in ("/{{ group }}/documents", "/{{ group }}/prompts", "/{{ group }}/memory"):
        assert retired_href not in navigation
    for retained_label in ("Agent Library", "Memory Channels", "Jobs", "Agents"):
        assert retained_label in navigation
    for mobile_contract in (
        'aria-label="Open navigation"',
        'aria-expanded="false"',
        'aria-controls="sidebar"',
        'aria-label="Close navigation"',
        "mobileMenuClose.focus()",
        "event.key === 'Escape'",
        "mobileMenuButton.focus()",
    ):
        assert mobile_contract in navigation


def test_setup_skill_strict_canonical_yaml_is_parseable_and_structurally_current():
    skill = (REPO_ROOT / "skills" / "agency-setup" / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"Use this canonical shape:\s*```yaml\n(?P<yaml>.*?)\n```", skill, re.DOTALL)
    assert match is not None
    yaml_text = match.group("yaml")
    assert "\t" not in yaml_text

    config = yaml.safe_load(yaml_text)
    assert "schema_version" not in config
    assert set(config["agency"]) >= {"agent_library", "compilation_cache", "memory_store"}
    assert config["memory"]["channels"]["project-strategy"]["display_name"] == "Project Strategy"
    group = config["groups"]["example"]
    assert "agents" not in group["dispatch"]
    assert group["runtime"]["sandbox"]["roots"]
    assert all({"name", "blueprint", "integration"} <= set(instance) for instance in group["agents"])
    builder = next(instance for instance in group["agents"] if instance["name"] == "builder")
    assert builder["runtime"]["sandbox"]["additional_roots"] == []
    selectors = [routine["memory"] for routine in builder["routines"]]
    assert {selector["scope"] for selector in selectors} == {"routine", "channel"}
    assert next(selector for selector in selectors if selector["scope"] == "channel")["channel"] == "project-strategy"


def test_local_links_in_active_documentation_resolve():
    missing: list[str] = []
    link_pattern = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
    for path in _active_documentation_paths():
        for raw_target in link_pattern.findall(path.read_text(encoding="utf-8")):
            target = raw_target.split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{path.relative_to(REPO_ROOT).as_posix()} -> {raw_target}")
    assert not missing, "\n".join(missing)
