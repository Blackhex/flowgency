from pathlib import Path

import pytest

from agency.configuration import ValidationFailed
from agency.integrations import REGISTRY
from tests._runtime_probe_helpers import (
    AI_CLI_COMMANDS,
    assert_live_success,
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
        assert integration.projector.validate_output(source, launch_dir) == ()
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
            label=f"{runtime.name}/basic",
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
        assert integration.projector.validate_output(source, launch_dir) == ()
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
            label=f"{runtime.name}/root-instructions",
        )