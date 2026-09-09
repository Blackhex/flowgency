from __future__ import annotations

from urllib.parse import urlparse


def research_definition() -> dict:
    return {
        "schema_version": 1,
        "id": "research-alt",
        "name": "Research alt",
        "description": "Alternative research workflow",
        "initial_state": "question",
        "states": [
            {"id": "question", "name": "Question", "color": "#9ca3af"},
            {"id": "done", "name": "Done", "color": "#7ad7bf"},
        ],
        "fields": [{"id": "research-notes", "label": "Notes", "type": "text"}],
        "transitions": [],
    }


def test_populated_workflow_storage_change_does_not_migrate(workflow_web_env):
    env = workflow_web_env
    ticket = env.create()
    original = env.read(ticket.ref).record

    response = env.save_settings(root=env.root_b)

    assert response.status_code == 303
    assert env.current_provider().list("newsletter", "board-a") == ()
    assert env.provider.read(ticket.ref) == original

    switch_back = env.save_settings(root=env.root_a)

    assert switch_back.status_code == 303
    assert env.read(ticket.ref).record == original


def test_name_only_save_does_not_require_current_storage_when_root_is_missing(
    workflow_web_env,
):
    env = workflow_web_env
    env.root_a.rmdir()

    response = env.save_settings(name="Renamed delivery")

    assert response.status_code == 303
    snapshot = env.store.load()
    workflow = snapshot.config.teams[env.team_id].workflows[env.workflow_id]
    assert workflow.name == "Renamed delivery"
    assert workflow.integration_config["root"] == str(env.root_a)


def test_incompatible_blueprint_save_preserves_editable_draft_and_binding(workflow_web_env):
    env = workflow_web_env
    env.create(title="Needs current state")
    env.write_blueprint("research-alt", research_definition())
    before = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]

    response = env.save_settings(
        name="Draft rename",
        blueprint="research-alt",
        root=env.root_a,
    )

    assert response.status_code == 422
    assert "Draft rename" in response.text
    assert "research-alt" in response.text
    after = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]
    assert after.blueprint == before.blueprint
    assert after.integration_config == before.integration_config


def test_stale_revision_preserves_submitted_draft_and_authoritative_binding(workflow_web_env):
    env = workflow_web_env
    stale_revision = env.store.load().revision
    env.set_storage_root(env.root_b)

    response = env.save_settings(
        name="Stale name",
        root=env.root_a,
        expected_revision=stale_revision,
    )

    assert response.status_code == 409
    assert "Stale name" in response.text
    assert str(env.root_a) in response.text
    workflow = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]
    assert workflow.integration_config["root"] == str(env.root_b)


def test_check_storage_distinguishes_empty_from_unavailable(workflow_web_env):
    env = workflow_web_env

    empty = env.check_storage(root=env.root_b)

    assert empty.status_code == 200
    assert empty.json()["status"] == "ok"
    assert empty.json()["ticket_count"] == 0

    env.root_b.rmdir()
    unavailable = env.check_storage(root=env.root_b)

    assert unavailable.status_code == 200
    assert unavailable.json()["status"] == "unavailable"
    assert unavailable.json()["ticket_count"] == 0


def test_storage_root_overlap_is_rejected_without_changing_binding(workflow_web_env):
    env = workflow_web_env
    overlap_root = env.store.load().config.teams[env.team_id].workspace_path
    before = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]

    response = env.save_settings(root=overlap_root)

    assert response.status_code == 422
    assert str(overlap_root) in response.text
    after = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]
    assert after.integration_config == before.integration_config


def test_check_storage_does_not_create_missing_root_or_change_config(workflow_web_env):
    env = workflow_web_env
    missing_root = env.root_a.parent / "delivery-read-only-check"
    config_bytes = env.store.path.read_bytes()

    response = env.check_storage(root=missing_root)

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert not missing_root.exists()
    assert env.store.path.read_bytes() == config_bytes


def test_invalid_edit_save_preserves_exact_submitted_draft_and_binding(workflow_web_env):
    env = workflow_web_env
    before = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]

    response = env.client.post(
        f"/{env.team_id}/workflows/{env.workflow_id}/settings",
        data={
            "workflow_id": env.workflow_id,
            "name": "",
            "blueprint": before.blueprint,
            "integration": before.integration,
            "integration_config.root": str(env.root_b),
            "expected_revision": env.store.load().revision,
        },
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert 'value=""' in response.text
    assert f'value="{env.root_b}"' in response.text
    after = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]
    assert after.name == before.name
    assert after.integration_config == before.integration_config


def test_create_ignores_forged_existing_workflow_id(workflow_web_env):
    env = workflow_web_env
    before = env.store.load().config.teams[env.team_id].workflows[env.workflow_id]

    response = env.client.post(
        f"/{env.team_id}/workflows/new",
        data={
            "workflow_id": env.workflow_id,
            "name": "Investigation",
            "blueprint": env.blueprint_id,
            "integration": "local",
            "integration_config.root": str(env.root_b),
            "expected_revision": env.store.load().revision,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location != f"/{env.team_id}/workflows/{env.workflow_id}/settings"
    created_id = urlparse(location).path.split("/")[-2]
    assert created_id.startswith("wf-")
    snapshot = env.store.load()
    assert snapshot.config.teams[env.team_id].workflows[env.workflow_id] == before
    created = snapshot.config.teams[env.team_id].workflows[created_id]
    assert created.name == "Investigation"
    assert created.blueprint == env.blueprint_id


def test_repeated_create_with_same_name_gets_distinct_server_ids(workflow_web_env):
    env = workflow_web_env
    created_ids: list[str] = []

    for forged_id, root in (("wf-fixed", env.root_b), (env.workflow_id, env.root_a.parent / "delivery-second")):
        root.mkdir(parents=True, exist_ok=True)
        response = env.client.post(
            f"/{env.team_id}/workflows/new",
            data={
                "workflow_id": forged_id,
                "name": "Repeated",
                "blueprint": env.blueprint_id,
                "integration": "local",
                "integration_config.root": str(root),
                "expected_revision": env.store.load().revision,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        created_ids.append(urlparse(response.headers["location"]).path.split("/")[-2])

    assert created_ids[0] != created_ids[1]
    assert all(created_id.startswith("wf-") for created_id in created_ids)