from __future__ import annotations

from pathlib import Path

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
                "schema_version": 2,
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
    app_mod.reload_groups()
    return TestClient(app_mod.app), tmp_path


def test_retired_routes_are_not_registered():
    registered = {
        (method, route.path)
        for route in app_mod.app.routes
        for method in getattr(route, "methods", set())
    }
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


def test_documentation_describes_only_strict_canonical_runtime_authority():
    required_docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "CLAUDE.md",
        REPO_ROOT / "config.yaml.example",
        REPO_ROOT / "kb" / "configuration.md",
        REPO_ROOT / "kb" / "directory-structure.md",
        REPO_ROOT / "kb" / "dispatch.md",
        REPO_ROOT / "kb" / "agent-identity.md",
        REPO_ROOT / "kb" / "integrations.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in required_docs)
    for required in (
        "schema_version: 2",
        "agent_library",
        "compilation_cache",
        "memory_store",
        "additional_roots",
        "routines:",
        "scope: routine",
        "scope: channel",
        "python tools/migrate_agent_model.py preview --config config.yaml --plan migration-plan.yaml",
        "python tools/migrate_agent_model.py apply --plan migration-plan.yaml",
        "python tools/migrate_agent_model.py verify --config config.yaml",
        "python tools/migrate_agent_model.py rollback --plan migration-plan.yaml",
    ):
        assert required in combined
    for forbidden in (
        "dispatch.agents",
        "auto-migrated to the `workspaces` list at config load time",
        "stores Agency metadata in `.agency-meta.yaml`",
        "store Agency metadata in `.agency-meta.yaml`",
        "`agents/{agent}/memory.md`",
    ):
        assert forbidden not in combined
