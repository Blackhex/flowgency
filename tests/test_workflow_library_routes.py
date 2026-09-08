from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from pathlib import Path

import yaml

from flowgency.workflows.forms import editor_payload
from tests._ticket_helpers import delivery_definition


def _settings_patch(flowgency, *, workflow_library: Path):
    from flowgency.configuration.patches import FlowgencySettingsPatch

    return FlowgencySettingsPatch(
        title=flowgency.title,
        default_team=flowgency.default_team,
        ai_backend=flowgency.ai_backend,
        theme="",
        dispatch_interval=15,
        agent_library=str(flowgency.agent_library),
        compilation_cache=str(flowgency.compilation_cache),
        memory_store=str(flowgency.memory_store),
        prompt_store=str(flowgency.prompt_store),
        workflow_library=str(workflow_library),
    )


class _EditorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tabs: list[str] = []
        self.labels: list[str] = []
        self.checkbox_labels: list[str] = []
        self.script_ids: list[str | None] = []
        self.script_bodies: dict[str, str] = {}
        self._capture_label = False
        self._current_label: list[str] = []
        self._in_tablist = False
        self._capture_tab = False
        self._current_tab: list[str] = []
        self._capture_script = False
        self._current_script_id: str | None = None
        self._current_script_body: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_map = dict(attrs)
        if tag == "nav" and attr_map.get("role") == "tablist":
            self._in_tablist = True
        elif tag == "button" and self._in_tablist and attr_map.get("role") == "tab":
            self._capture_tab = True
            self._current_tab = []
        elif tag == "label":
            self._capture_label = True
            self._current_label = []
        elif tag == "script":
            self._capture_script = True
            self._current_script_id = attr_map.get("id")
            self._current_script_body = []
            self.script_ids.append(self._current_script_id)
        elif tag == "input" and attr_map.get("type") == "checkbox":
            aria = attr_map.get("aria-label")
            if aria:
                self.checkbox_labels.append(aria)

    def handle_data(self, data: str) -> None:
        if self._capture_tab:
            self._current_tab.append(data)
        if self._capture_label:
            self._current_label.append(data)
        if self._capture_script:
            self._current_script_body.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._in_tablist:
            self._in_tablist = False
        elif tag == "button" and self._capture_tab:
            label = " ".join(part.strip() for part in self._current_tab if part.strip())
            if label:
                self.tabs.append(label)
            self._capture_tab = False
            self._current_tab = []
        elif tag == "label" and self._capture_label:
            label = " ".join(part.strip() for part in self._current_label if part.strip())
            if label:
                self.labels.append(label)
            self._capture_label = False
            self._current_label = []
        elif tag == "script" and self._capture_script:
            if self._current_script_id is not None:
                self.script_bodies[self._current_script_id] = "".join(self._current_script_body)
            self._capture_script = False
            self._current_script_id = None
            self._current_script_body = []


def _parse_editor(html: str) -> _EditorParser:
    parser = _EditorParser()
    parser.feed(html)
    return parser


def test_workflow_library_page_lists_current_sources(workflow_web_env):
    env = workflow_web_env

    response = env.client.get("/admin/workflow-library")

    assert response.status_code == 200
    assert "Workflow Library" in response.text
    assert "Delivery" in response.text


def test_workflow_blueprint_editor_hides_identifier_source_and_evidence_controls(
    workflow_web_env,
):
    env = workflow_web_env

    response = env.client.get("/admin/workflow-library/blueprints/delivery")

    assert response.status_code == 200
    parser = _parse_editor(response.text)
    assert parser.tabs == ["Overview", "States", "Transitions"]
    assert all("Identifier" not in label for label in parser.labels)
    assert all("Source" not in label for label in parser.labels)
    assert all(
        "Evidence required" not in label for label in parser.checkbox_labels
    )


def test_preview_route_reports_validation_without_writing_source(workflow_web_env):
    env = workflow_web_env
    source = env.library.inspect("delivery")
    payload = {
        "expected_revision": env.store.load().revision,
        "expected_digest": source.digest,
        "draft_version": 2,
        "draft": editor_payload(source),
    }
    payload["draft"]["states"] = payload["draft"]["states"][:-1]

    before = source.source_path.read_text(encoding="utf-8")
    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery/preview",
        data={"payload": json.dumps(payload)},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["draft_version"] == 2
    assert response.json()["draft"]["states"] == payload["draft"]["states"]
    assert source.source_path.read_text(encoding="utf-8") == before


def test_edit_route_save_redirects_and_publishes_current_source(workflow_web_env):
    env = workflow_web_env
    source = env.library.inspect("delivery")
    payload = {
        "expected_revision": env.store.load().revision,
        "expected_digest": source.digest,
        "draft_version": 3,
        "draft": editor_payload(source),
    }
    payload["draft"]["name"] = "Delivery workflow"
    payload["draft"]["description"] = "Published from the structured editor"

    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/workflow-library/blueprints/delivery"
    published = yaml.safe_load(source.source_path.read_text(encoding="utf-8"))
    assert published["name"] == "Delivery workflow"
    assert published["description"] == "Published from the structured editor"


def test_edit_route_conflict_rerenders_html_with_submitted_draft(workflow_web_env):
    env = workflow_web_env
    source = env.library.inspect("delivery")
    payload = {
        "expected_revision": env.store.load().revision,
        "expected_digest": source.digest,
        "draft_version": 4,
        "draft": editor_payload(source),
    }
    payload["draft"]["name"] = "My local draft"
    changed = source.definition.model_copy(update={"description": "external change"})
    env.library.write_candidate("delivery", source.digest, changed)

    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": json.dumps(payload)},
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "My local draft" in response.text
    assert "reload" in response.text.lower()


def test_create_route_writes_new_blueprint_and_redirects(workflow_web_env):
    env = workflow_web_env

    response = env.client.post(
        "/admin/workflow-library/blueprints/new",
        data={
            "payload": json.dumps(
                {
                    "expected_revision": env.store.load().revision,
                    "draft_version": 1,
                    "draft": {
                        "schema_version": 1,
                        "id": "__new__",
                        "name": "Research intake",
                        "description": "Created from the workflow editor",
                        "states": [
                            {
                                "key": "state-1",
                                "existing_state_id": None,
                                "name": "Queued",
                                "color": "#9ca3af",
                                "initial": True,
                            }
                        ],
                        "fields": [],
                        "transitions": [],
                    },
                }
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    created_id = response.headers["location"].rsplit("/", 1)[-1]
    assert re.fullmatch(r"wf-[0-9a-f]{32}", created_id)
    created = env.library.inspect(created_id)
    assert created.definition.id == created_id
    assert created.definition.name == "Research intake"


def test_create_route_allocates_generated_source_id_and_allows_same_label_reuse(
    workflow_web_env,
):
    env = workflow_web_env

    def create_payload() -> dict[str, object]:
        return {
            "expected_revision": env.store.load().revision,
            "draft_version": 1,
            "draft": {
                "schema_version": 1,
                "id": "__new__",
                "name": "Research intake",
                "description": "Created from the workflow editor",
                "states": [
                    {
                        "key": "state-1",
                        "existing_state_id": None,
                        "name": "Queued",
                        "color": "#9ca3af",
                        "initial": True,
                    }
                ],
                "fields": [],
                "transitions": [],
            },
        }

    first = env.client.post(
        "/admin/workflow-library/blueprints/new",
        data={"payload": json.dumps(create_payload())},
        follow_redirects=False,
    )

    assert first.status_code == 303
    first_id = first.headers["location"].rsplit("/", 1)[-1]
    assert re.fullmatch(r"wf-[0-9a-f]{32}", first_id)
    created = env.library.inspect(first_id)
    assert created.definition.id == first_id
    assert created.definition.name == "Research intake"

    update = {
        "expected_revision": env.store.load().revision,
        "expected_digest": created.digest,
        "draft_version": 2,
        "draft": editor_payload(created),
    }
    update["draft"]["name"] = "Renamed research intake"

    renamed = env.client.post(
        f"/admin/workflow-library/blueprints/{first_id}",
        data={"payload": json.dumps(update)},
        follow_redirects=False,
    )

    assert renamed.status_code == 303
    assert renamed.headers["location"] == f"/admin/workflow-library/blueprints/{first_id}"
    assert env.library.inspect(first_id).definition.name == "Renamed research intake"

    second = env.client.post(
        "/admin/workflow-library/blueprints/new",
        data={"payload": json.dumps(create_payload())},
        follow_redirects=False,
    )

    assert second.status_code == 303
    second_id = second.headers["location"].rsplit("/", 1)[-1]
    assert re.fullmatch(r"wf-[0-9a-f]{32}", second_id)
    assert second_id != first_id
    assert env.library.inspect(second_id).definition.name == "Research intake"


def test_preview_new_route_validates_without_writing_source(workflow_web_env):
    env = workflow_web_env
    before = sorted(path.relative_to(env.library.root) for path in env.library.root.rglob("workflow.yaml"))

    response = env.client.post(
        "/admin/workflow-library/blueprints/new/preview",
        data={
            "payload": json.dumps(
                {
                    "expected_revision": env.store.load().revision,
                    "expected_digest": None,
                    "draft_version": 6,
                    "draft": {
                        "schema_version": 1,
                        "id": "__new__",
                        "name": "Preview only",
                        "description": "Preview without publishing",
                        "states": [
                            {
                                "key": "state-1",
                                "existing_state_id": None,
                                "name": "Queued",
                                "color": "#9ca3af",
                                "initial": True,
                            }
                        ],
                        "fields": [],
                        "transitions": [],
                    },
                }
            )
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["draft_version"] == 6
    assert response.json()["issues"] == []
    after = sorted(path.relative_to(env.library.root) for path in env.library.root.rglob("workflow.yaml"))
    assert after == before


def test_preview_new_route_ignores_posted_draft_id_without_writing_source(
    workflow_web_env,
):
    env = workflow_web_env
    before = sorted(path.relative_to(env.library.root) for path in env.library.root.rglob("workflow.yaml"))

    response = env.client.post(
        "/admin/workflow-library/blueprints/new/preview",
        data={
            "payload": json.dumps(
                {
                    "expected_revision": env.store.load().revision,
                    "expected_digest": None,
                    "draft_version": 9,
                    "draft": {
                        "schema_version": 1,
                        "id": "wf-forged-client-id",
                        "name": "Preview only",
                        "description": "Preview without publishing",
                        "states": [
                            {
                                "key": "state-1",
                                "existing_state_id": None,
                                "name": "Queued",
                                "color": "#9ca3af",
                                "initial": True,
                            }
                        ],
                        "fields": [],
                        "transitions": [],
                    },
                }
            )
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["draft_version"] == 9
    assert response.json()["issues"] == []
    after = sorted(path.relative_to(env.library.root) for path in env.library.root.rglob("workflow.yaml"))
    assert after == before


def test_save_route_rejects_invalid_draft_version_without_secondary_error(
    workflow_web_env,
):
    env = workflow_web_env
    source = env.library.inspect("delivery")
    payload = {
        "expected_revision": env.store.load().revision,
        "expected_digest": source.digest,
        "draft_version": "oops",
        "draft": editor_payload(source),
    }

    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": json.dumps(payload)},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["draft"] == payload["draft"]
    assert any(issue["field"] == "draft_version" for issue in body["issues"])


def test_save_route_reports_malformed_json_in_html_when_source_is_unavailable(
    workflow_web_env,
):
    env = workflow_web_env
    source = env.library.inspect("delivery")
    before = "not: [valid"
    source.source_path.write_text(before, encoding="utf-8")

    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": "{"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 422
    assert "Payload must be valid JSON." in response.text
    assert source.source_path.read_text(encoding="utf-8") == before


def test_save_route_reports_malformed_json_in_json_when_source_is_unavailable(
    workflow_web_env,
):
    env = workflow_web_env
    source = env.library.inspect("delivery")
    before = "not: [valid"
    source.source_path.write_text(before, encoding="utf-8")

    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": "{"},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["issues"][0]["code"] == "invalid-request"
    assert source.source_path.read_text(encoding="utf-8") == before


def test_create_route_ignores_posted_generated_id_and_allocates_server_id(
    workflow_web_env,
):
    env = workflow_web_env
    forged_id = "wf-1234567890abcdef1234567890abcdef"

    response = env.client.post(
        "/admin/workflow-library/blueprints/new",
        data={
            "payload": json.dumps(
                {
                    "expected_revision": env.store.load().revision,
                    "draft_version": 10,
                    "draft": {
                        "schema_version": 1,
                        "id": forged_id,
                        "name": "Research intake",
                        "description": "Created from the workflow editor",
                        "states": [
                            {
                                "key": "state-1",
                                "existing_state_id": None,
                                "name": "Queued",
                                "color": "#9ca3af",
                                "initial": True,
                            }
                        ],
                        "fields": [],
                        "transitions": [],
                    },
                }
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    created_id = response.headers["location"].rsplit("/", 1)[-1]
    assert re.fullmatch(r"wf-[0-9a-f]{32}", created_id)
    assert created_id != forged_id
    created = env.library.inspect(created_id)
    assert created.definition.id == created_id
    assert created.definition.name == "Research intake"


def test_editor_page_escapes_script_terminators_inside_initial_json(workflow_web_env):
    env = workflow_web_env
    sentinel = '</script><script id="editor-injected">window.injected = true</script>'
    env.write_blueprint(
        "script-safe",
        {
            "schema_version": 1,
            "id": "script-safe",
            "name": f"Workflow {sentinel}",
            "description": f"Description {sentinel}",
            "initial_state": "review",
            "states": [
                {"id": "review", "name": "Review", "color": "#ebc77c"},
                {"id": "done", "name": "Done", "color": "#7ad7bf"},
            ],
            "fields": [],
            "transitions": [
                {
                    "id": "approve",
                    "name": "Approve",
                    "from_state": "review",
                    "to_state": "done",
                    "inputs": [],
                    "outputs": [],
                    "preconditions": [],
                    "criteria": [
                        {"id": "criterion-a", "description": f"Criterion {sentinel}"}
                    ],
                }
            ],
        },
    )

    response = env.client.get("/admin/workflow-library/blueprints/script-safe")

    assert response.status_code == 200
    parser = _parse_editor(response.text)
    assert parser.script_ids.count("workflow-editor-data") == 1
    assert "editor-injected" not in [script_id for script_id in parser.script_ids if script_id is not None]
    initial = json.loads(parser.script_bodies["workflow-editor-data"])
    assert initial["draft"]["name"] == f"Workflow {sentinel}"
    assert initial["draft"]["description"] == f"Description {sentinel}"
    assert (
        initial["draft"]["transitions"][0]["criteria"][0]["description"]
        == f"Criterion {sentinel}"
    )


def test_save_route_preserves_submitted_draft_when_source_is_malformed(
    workflow_web_env,
):
    env = workflow_web_env
    source = env.library.inspect("delivery")
    payload = {
        "expected_revision": env.store.load().revision,
        "expected_digest": source.digest,
        "draft_version": 7,
        "draft": editor_payload(source),
    }
    payload["draft"]["name"] = "Keep this local draft"
    source.source_path.write_text("not: [valid", encoding="utf-8")

    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": json.dumps(payload)},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["draft_version"] == 7
    assert body["draft"]["name"] == "Keep this local draft"
    assert body["issues"][0]["code"] == "source-unavailable"


def test_save_route_uses_current_workflow_library_root(workflow_web_env, tmp_path):
    env = workflow_web_env
    from flowgency.configuration.patches import patch_flowgency_settings

    moved_root = tmp_path / "workflow-library-next"
    (moved_root / "delivery").mkdir(parents=True)
    moved_path = moved_root / "delivery" / "workflow.yaml"
    moved_path.write_text(
        yaml.safe_dump(delivery_definition(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    snapshot = env.store.load()
    patch_flowgency_settings(
        env.store,
        snapshot.revision,
        _settings_patch(snapshot.config.flowgency, workflow_library=moved_root),
    )

    source = yaml.safe_load(moved_path.read_text(encoding="utf-8"))
    library_source = env.configuration_service.library_for(env.store.load()).inspect("delivery")
    payload = {
        "expected_revision": env.store.load().revision,
        "expected_digest": library_source.digest,
        "draft_version": 8,
        "draft": editor_payload(library_source),
    }
    payload["draft"]["description"] = "Saved into moved root"

    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert yaml.safe_load(moved_path.read_text(encoding="utf-8"))["description"] == "Saved into moved root"
    assert yaml.safe_load(env.library.source_path("delivery").read_text(encoding="utf-8")) == source


def test_workflow_library_list_shows_invalid_blueprint_without_hiding_valid_ones(
    workflow_web_env,
):
    env = workflow_web_env
    broken = env.library.root / "broken"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "workflow.yaml").write_text("not: [valid", encoding="utf-8")

    response = env.client.get("/admin/workflow-library")

    assert response.status_code == 200
    assert "Delivery" in response.text
    assert "broken" in response.text


def test_save_route_rejects_incompatible_state_removal_without_writing_source(
    workflow_web_env,
):
    env = workflow_web_env
    env.seed_ticket(state_id="review")
    source = env.library.inspect("delivery")
    payload = {
        "expected_revision": env.store.load().revision,
        "expected_digest": source.digest,
        "draft_version": 5,
        "draft": editor_payload(source),
    }
    payload["draft"]["states"] = [
        state
        for state in payload["draft"]["states"]
        if state["existing_state_id"] == "done"
    ]
    payload["draft"]["states"][0]["initial"] = True
    payload["draft"]["transitions"] = []

    before = source.source_path.read_text(encoding="utf-8")
    response = env.client.post(
        "/admin/workflow-library/blueprints/delivery",
        data={"payload": json.dumps(payload)},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 409
    assert source.source_path.read_text(encoding="utf-8") == before