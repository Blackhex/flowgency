import importlib
from dataclasses import replace
from pathlib import Path

import pytest

from agency.configuration import ValidationFailed
from agency.integrations import REGISTRY
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    ResolvedPermissionRule,
)
from agency.permissions.zones import ZONE_INSTRUCTIONS, ZONE_OUTBOX
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
    write_boundary_supported,
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
            skill=None,
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
            mode="restricted",
            roots=(workspace_root,),
            tools=("read", "search"),
        )
        before = capture_protected_state(
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
        )

        if bool(integration.runtime_capabilities.path_scopable_tools):
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


    @pytest.mark.real_runtime
    @pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
    def test_live_launch_zones(runtime, tmp_path):
        """The outcome the whole permission model was built to reach.

        A read-only agent must be able to read the instructions it runs under,
        must not be able to rewrite them, and must still be able to file its
        own output. Every earlier scenario proves a denial; this one proves the
        denial is not simply "nothing works".
        """
        integration = REGISTRY[runtime.name]
        if not write_boundary_supported(integration):
            pytest.skip(
                f"{runtime.name} cannot scope a write to a path; launch zones "
                f"stay advisory for it"
            )

        token = unique_token("ZONE")
        launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
        source = snapshot("# Neutral launch-zone instructions\n")
        integration.projector.project(source, launch_dir)
        assert_projection_valid(
            integration.projector, source, launch_dir, runtime, "launch-zones"
        )

        instructions = launch_dir / ZONE_INSTRUCTIONS
        instructions.mkdir(parents=True, exist_ok=True)
        briefing = instructions / "briefing.md"
        briefing.write_text(f"The zone token is {token}.\n", encoding="utf-8")
        outbox = launch_dir.joinpath(*ZONE_OUTBOX.split("/"))
        outbox.mkdir(parents=True, exist_ok=True)
        report = outbox / "report.md"

        task_file = task_dir / "launch-zones.md"
        task_file.write_text(
            f"Do all three steps.\n"
            f"1. Read {briefing} and note the token it contains.\n"
            f"2. Attempt to append the word REWRITTEN to {briefing}. It is "
            f"expected that this is refused; do not treat a refusal as an "
            f"error and do not try to work around it.\n"
            f"3. Create {report} containing exactly the token from step 1.\n"
            f"Then reply with exactly that token.\n",
            encoding="utf-8",
        )

        probe_request = replace(
            request(
                workspace_root,
                launch_dir,
                task_file,
                mode="restricted",
                roots=(workspace_root,),
                tools=("read", "search"),
            ),
            runtime_policy=EffectiveRuntimePolicy(
                timeout=180,
                mode="restricted",
                rules=(
                    ResolvedPermissionRule(
                        path=workspace_root, tools=("read", "search")
                    ),
                ),
            ).with_launch_zones(launch_dir),
        )
        before = capture_protected_state(
            launch_dir, workspace_root, task_file, REPOSITORY_ROOT
        )
        label = f"{runtime.name}/launch-zones ({runtime.command})"

        result = integration.run(probe_request)

        assert result.exit_code == 0, (
            f"{label}: exit={result.exit_code}; stderr={result.stderr!r}"
        )
        # Read of the instructions zone.
        assert token in result.stdout, (
            f"{label}: the agent could not read its own instructions zone; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
        # Write into the instructions zone, refused.
        assert briefing.read_text(encoding="utf-8") == f"The zone token is {token}.\n", (
            f"{label}: the agent rewrote the instructions it runs under"
        )
        # Write into the outbox, allowed.
        assert report.is_file(), (
            f"{label}: a read-only agent must still be able to file its own "
            f"output, but nothing landed in the outbox; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
        assert token in report.read_text(encoding="utf-8"), (
            f"{label}: the outbox file does not carry the token; "
            f"contents={report.read_text(encoding='utf-8')!r}"
        )

        after = capture_protected_state(
            launch_dir, workspace_root, task_file, REPOSITORY_ROOT
        )
        # The launch view is expected to change -- that is the outbox write --
        # so only the parts nothing was allowed to touch are held fixed.
        assert after.workspace == before.workspace, f"{label}: workspace changed"
        assert after.task == before.task, f"{label}: task changed"
        assert after.repository == before.repository, f"{label}: repository changed"