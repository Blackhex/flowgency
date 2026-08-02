import importlib
from pathlib import Path

import pytest

from agency.configuration import ValidationFailed
from agency.integrations import REGISTRY
from tests._runtime_probe_helpers import (
    AI_CLI_COMMANDS,
    assert_live_success,
    assert_projection_valid,
    assert_protected_state_unchanged,
    capture_protected_state,
    create_probe_directories,
    installed_ai_cli_runtimes,
    request,
    selected_skill_supported,
    snapshot,
    unique_token,
)


INSTALLED_RUNTIMES = installed_ai_cli_runtimes()
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def guard_against_task_read(monkeypatch, task_file: Path) -> None:
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        if path == task_file:
            raise AssertionError("scenario read task before validation rejection")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)


if not INSTALLED_RUNTIMES:

    @pytest.mark.real_runtime
    def test_no_supported_ai_cli_is_installed():
        pytest.skip(
            "No supported AI CLI is installed; expected one of: "
            + ", ".join(AI_CLI_COMMANDS.values())
        )

else:

    @pytest.mark.real_runtime
    @pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
    def test_live_basic_execution(runtime, tmp_path):
        integration = REGISTRY[runtime.name]
        token = unique_token("BASIC")
        launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
        source = snapshot("# Neutral runtime probe\n")
        integration.projector.project(source, launch_dir)
        assert_projection_valid(
            integration.projector,
            source,
            launch_dir,
            runtime,
            "basic",
        )
        task_file = task_dir / "basic.md"
        task_file.write_text(
            f"Reply with exactly {token}. Do not use tools or modify files.\n",
            encoding="utf-8",
        )
        before = capture_protected_state(
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
        )

        result = integration.run(request(workspace_root, launch_dir, task_file))

        assert_live_success(result, runtime, "basic", token)
        assert_protected_state_unchanged(
            before,
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
            runtime=runtime,
            scenario="basic",
        )


    @pytest.mark.real_runtime
    @pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
    def test_live_root_instructions(runtime, tmp_path):
        integration = REGISTRY[runtime.name]
        token = unique_token("INSTRUCTION")
        launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
        source = snapshot(
            "# Runtime probe\n\nReply with this exact instruction token when asked: "
            f"{token}\n"
        )
        integration.projector.project(source, launch_dir)
        assert_projection_valid(
            integration.projector,
            source,
            launch_dir,
            runtime,
            "root-instructions",
        )
        task_file = task_dir / "instructions.md"
        task_file.write_text(
            "Follow the projected root instructions and return their exact token. "
            "Do not use tools or modify files.\n",
            encoding="utf-8",
        )
        before = capture_protected_state(
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
        )

        result = integration.run(request(workspace_root, launch_dir, task_file))

        assert_live_success(result, runtime, "root-instructions", token)
        assert_protected_state_unchanged(
            before,
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
            runtime=runtime,
            scenario="root-instructions",
        )


    @pytest.mark.real_runtime
    @pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
    def test_live_selected_skill(runtime, tmp_path, monkeypatch):
        integration = REGISTRY[runtime.name]
        token = unique_token("SKILL")
        launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
        source = snapshot(
            "# Neutral root instructions\n",
            skill=f"Return this exact selected-skill token: {token}",
        )
        integration.projector.project(source, launch_dir)
        assert_projection_valid(
            integration.projector,
            source,
            launch_dir,
            runtime,
            "selected-skill",
        )
        task_file = task_dir / "selected-skill.md"
        task_file.write_text(
            "Use the explicitly selected skill and return its exact "
            "token. Do not use tools or modify files.\n",
            encoding="utf-8",
        )
        probe_request = request(
            workspace_root,
            launch_dir,
            task_file,
            skill="runtime-probe",
        )
        before = capture_protected_state(
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
        )

        if selected_skill_supported(integration):
            result = integration.run(probe_request)
            assert_live_success(result, runtime, "selected-skill", token)
        else:
            integration_module = importlib.import_module(type(integration).__module__)

            with monkeypatch.context() as scoped:
                guard_against_task_read(scoped, task_file)

                def unexpected_resolution():
                    raise AssertionError(
                        "scenario re-resolved executable before rejection"
                    )

                def unexpected_subprocess_run(*args, **kwargs):
                    raise AssertionError(
                        "scenario launched subprocess before validation rejection"
                    )

                scoped.setattr(
                    integration,
                    "resolve_executable",
                    unexpected_resolution,
                )
                scoped.setattr(
                    integration_module.subprocess,
                    "run",
                    unexpected_subprocess_run,
                )
                with pytest.raises(ValidationFailed) as excinfo:
                    integration.run(probe_request)
            assert [issue.code for issue in excinfo.value.issues] == [
                "unsupported-skill-activation"
            ]

        assert_protected_state_unchanged(
            before,
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
            runtime=runtime,
            scenario="selected-skill",
        )


    @pytest.mark.real_runtime
    @pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
    def test_live_write_boundary(runtime, tmp_path, monkeypatch):
        integration = REGISTRY[runtime.name]
        token = unique_token("WRITE")
        launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
        source = snapshot("# Neutral write-boundary instructions\n")
        integration.projector.project(source, launch_dir)
        assert_projection_valid(
            integration.projector,
            source,
            launch_dir,
            runtime,
            "write-boundary",
        )
        target = workspace_root / "write-probe.txt"
        task_file = task_dir / "write-boundary.md"
        task_file.write_text(
            f"Attempt to create {target} containing {token}. Regardless of whether "
            f"the write is denied, reply with exactly {token}.\n",
            encoding="utf-8",
        )
        probe_request = request(
            workspace_root,
            launch_dir,
            task_file,
            sandbox_mode="restricted",
            sandbox_roots=(workspace_root,),
            writable_roots=(),
            tool_mode="allowlist",
            tool_names=("read", "search"),
        )
        before = capture_protected_state(
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
        )

        if integration.runtime_capabilities.enforces_write_boundary:
            result = integration.run(probe_request)
            assert_live_success(result, runtime, "write-boundary", token)
            # Whether the model tries the denied write is its own choice; the boundary
            # is that nothing outside the probe is attempted and nothing lands.
            assert result.write_attempts in ([], ["write-probe.txt"]), (
                f"{runtime.name}/write-boundary ({runtime.command}): the only permissible "
                f"write attempt is ['write-probe.txt'] (or none, when the model declines to "
                f"try); actual attempts={result.write_attempts!r}; "
                f"changed_files={result.changed_files!r}; stdout={result.stdout!r}; stderr={result.stderr!r}"
            )
        else:
            # The permission model enforces write boundaries through rule-based
            # tool grants.  Integrations that support the requested mode accept
            # the policy; the rules themselves restrict what tools are available.
            policy_issues = integration.validate_runtime_policy(probe_request.runtime_policy)
            if policy_issues:
                integration_module = importlib.import_module(type(integration).__module__)
                with monkeypatch.context() as scoped:
                    guard_against_task_read(scoped, task_file)

                    scoped.setattr(
                        integration,
                        "resolve_executable",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("scenario re-resolved executable before rejection")
                        ),
                    )
                    scoped.setattr(
                        integration_module.subprocess,
                        "run",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("scenario launched subprocess before validation rejection")
                        ),
                    )
                    with pytest.raises(ValidationFailed):
                        integration.run(probe_request)
            else:
                pytest.skip(
                    f"{runtime.name} accepts the policy; write boundary is "
                    f"rule-enforced, not integration-rejected"
                )
            assert_protected_state_unchanged(
                before,
                launch_dir,
                workspace_root,
                task_file,
                REPOSITORY_ROOT,
                runtime=runtime,
                scenario="write-boundary",
            )

        assert not target.exists()
        assert_protected_state_unchanged(
            before,
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
            runtime=runtime,
            scenario="write-boundary",
        )