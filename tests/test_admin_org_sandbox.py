import asyncio

import agency.app as app_mod


class FakeForm(dict):
    def getlist(self, k):
        v = self.get(k, [])
        return v if isinstance(v, list) else [v]


class FakeRequest:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_admin_org_save_persists_sandbox_root(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"), "agents": []}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": str(tmp_path / "repo"),
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert saved["groups"]["grp"]["sandbox_root"] == str(tmp_path / "repo")


def test_admin_org_save_clears_sandbox_root_when_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"),
                            "agents": [], "sandbox_root": "/old/root"}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": "",
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert "sandbox_root" not in saved["groups"]["grp"]


def test_admin_org_create_persists_sandbox_root(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {},
    })
    app_mod.reload_groups()

    # Create the agents directory so handler takes success path
    (tmp_path / "agents").mkdir()

    form = FakeForm({
        "key": "new",
        "name": "New Group",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "sandbox_root": str(tmp_path / "repo"),
    })

    _run(app_mod.admin_org_create(FakeRequest(form)))

    saved = app_mod.load_config()
    assert saved["groups"]["new"]["sandbox_root"] == str(tmp_path / "repo")


def test_admin_org_create_omits_sandbox_root_when_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {},
    })
    app_mod.reload_groups()

    # Create the agents directory so handler takes success path
    (tmp_path / "agents").mkdir()

    form = FakeForm({
        "key": "new",
        "name": "New Group",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "sandbox_root": "",
    })

    _run(app_mod.admin_org_create(FakeRequest(form)))

    saved = app_mod.load_config()
    assert "sandbox_root" not in saved["groups"]["new"]


def test_admin_org_save_persists_multiline_sandbox_root_as_list(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"), "agents": []}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert saved["groups"]["grp"]["sandbox_root"] == [
        str(tmp_path / "repo"), str(tmp_path / "cowork")
    ]


def test_admin_org_save_single_line_sandbox_root_stays_string(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"), "agents": []}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": str(tmp_path / "repo"),
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert saved["groups"]["grp"]["sandbox_root"] == str(tmp_path / "repo")


def test_admin_org_save_persists_allowed_tools(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"), "agents": []}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": str(tmp_path / "repo"),
        "allowed_tools": ["shell", "write"],
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert saved["groups"]["grp"]["allowed_tools"] == ["shell", "write"]


def test_admin_org_save_clears_allowed_tools_when_none_checked(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"),
                            "agents": [], "allowed_tools": ["shell"]}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": "",
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert "allowed_tools" not in saved["groups"]["grp"]


def test_admin_org_save_preserves_metadata_for_retained_agents(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    agents_path = tmp_path / "agents"
    agents_path.mkdir()
    external_path = tmp_path / "shared" / "core-builder"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    core_builder = {
        "name": "core-builder",
        "integration": "copilot",
        "path": str(external_path),
        "integration_config": {"model": "gpt-5"},
    }
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {
            "grp": {
                "name": "Grp",
                "path": str(agents_path),
                "default_integration": "copilot",
                "agents": [core_builder, "superseded", {"name": "removed", "integration": "codex"}],
            }
        },
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(agents_path),
        "agents": "superseded\ncore-builder\nnew-builder",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": "",
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert saved["groups"]["grp"]["agents"] == [
        "superseded",
        core_builder,
        "new-builder",
    ]


def test_admin_org_create_persists_multiline_and_tools(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {},
    })
    app_mod.reload_groups()

    (tmp_path / "agents").mkdir()

    form = FakeForm({
        "key": "new",
        "name": "New Group",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "sandbox_root": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
        "allowed_tools": ["shell", "write"],
    })

    _run(app_mod.admin_org_create(FakeRequest(form)))

    saved = app_mod.load_config()
    assert saved["groups"]["new"]["sandbox_root"] == [
        str(tmp_path / "repo"), str(tmp_path / "cowork")
    ]
    assert saved["groups"]["new"]["allowed_tools"] == ["shell", "write"]
