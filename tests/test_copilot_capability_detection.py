from __future__ import annotations

import subprocess

import pytest

from agency.integrations.agency import copilot as copilot_module
from agency.integrations.agency.copilot import CopilotIntegration


MEASURED_VERSION = "1.0.78-2"


@pytest.fixture
def copilot():
    # A throwaway instance: stubbing the registered one would leave instance
    # attributes behind that shadow later class-level patches.
    return CopilotIntegration()


def stub_version(monkeypatch, integration, version):
    """Answer the version probe without an installed CLI."""
    monkeypatch.setattr(integration, "resolve_executable", lambda: "copilot.exe")
    monkeypatch.setattr(integration, "_probe_cli_version", lambda command: version)
    monkeypatch.setattr(integration, "_executable_stamp", lambda command: command)


def test_the_measured_version_scopes_write(copilot, monkeypatch):
    stub_version(monkeypatch, copilot, MEASURED_VERSION)

    assert "write" in copilot.runtime_capabilities.path_scopable_tools


def test_a_later_version_in_the_same_line_scopes_write(copilot, monkeypatch):
    stub_version(monkeypatch, copilot, "1.4.0-1")

    assert "write" in copilot.runtime_capabilities.path_scopable_tools


def test_a_version_below_the_measured_one_scopes_nothing(copilot, monkeypatch):
    stub_version(monkeypatch, copilot, "1.0.64-1")

    assert copilot.runtime_capabilities.path_scopable_tools == frozenset()


def test_the_next_major_line_scopes_nothing(copilot, monkeypatch):
    # A major bump is the one signal the publisher gives that behaviour may
    # change, so the sandbox claim has to be re-measured before it is renewed.
    stub_version(monkeypatch, copilot, "2.0.0-1")

    assert copilot.runtime_capabilities.path_scopable_tools == frozenset()


def test_an_unparseable_version_scopes_nothing(copilot, monkeypatch):
    stub_version(monkeypatch, copilot, "copilot (development build)")

    assert copilot.runtime_capabilities.path_scopable_tools == frozenset()


def test_the_real_banner_is_read_as_a_version(copilot, monkeypatch):
    # What the installed CLI actually answers: a sentence, a trailing full
    # stop, and an upgrade notice underneath. Requiring the whole output to be
    # a bare version number silently drops the claim on every real install.
    stub_version(
        monkeypatch,
        copilot,
        "GitHub Copilot CLI 1.0.78-2.\nRun 'copilot update' to check for updates.",
    )

    assert "write" in copilot.runtime_capabilities.path_scopable_tools


def test_a_banner_containing_a_version_is_not_read_as_one(copilot, monkeypatch):
    # Matching anywhere would let a "latest is 1.4.0" upgrade notice stand in
    # for the installed version, renewing a claim that was never measured.
    stub_version(monkeypatch, copilot, "update available: 1.4.0-1 (you have 1.0.1-1)")

    assert copilot.runtime_capabilities.path_scopable_tools == frozenset()


def test_an_upgrade_notice_below_the_banner_is_ignored(copilot, monkeypatch):
    stub_version(
        monkeypatch,
        copilot,
        "GitHub Copilot CLI 1.0.64-1.\nA new version 1.4.0-1 is available.",
    )

    assert copilot.runtime_capabilities.path_scopable_tools == frozenset()


def test_a_release_without_a_build_suffix_is_the_newest_of_its_patch(
    copilot, monkeypatch
):
    # The suffix is a pre-release build counter, so 1.0.78 ships after
    # 1.0.78-2 rather than before it. Ordering it as build 0 would retire the
    # claim on exactly the release the measurement was taken against.
    stub_version(monkeypatch, copilot, "1.0.78")

    assert "write" in copilot.runtime_capabilities.path_scopable_tools


def test_an_absent_cli_scopes_nothing_without_probing(copilot, monkeypatch):
    monkeypatch.setattr(copilot, "resolve_executable", lambda: None)
    monkeypatch.setattr(
        copilot,
        "_probe_cli_version",
        lambda command: pytest.fail("probed a CLI that is not installed"),
    )

    assert copilot.runtime_capabilities.path_scopable_tools == frozenset()


def test_a_probe_that_raises_scopes_nothing(copilot, monkeypatch):
    def exploding(command):
        raise OSError("cli refused to start")

    monkeypatch.setattr(copilot, "resolve_executable", lambda: "copilot.exe")
    monkeypatch.setattr(copilot, "_executable_stamp", lambda command: command)
    monkeypatch.setattr(copilot, "_probe_cli_version", exploding)

    caps = copilot.runtime_capabilities

    assert caps.path_scopable_tools == frozenset()
    assert caps.permission_modes == frozenset({"restricted", "unrestricted"})


def test_the_cache_key_is_the_detected_version(copilot, monkeypatch):
    stub_version(monkeypatch, copilot, MEASURED_VERSION)

    assert copilot._capability_cache_key() == MEASURED_VERSION


def test_an_absent_cli_yields_no_cache_key(copilot, monkeypatch):
    monkeypatch.setattr(copilot, "resolve_executable", lambda: None)

    assert copilot._capability_cache_key() is None


def test_the_version_is_probed_once_per_installed_binary(copilot, monkeypatch):
    calls = {"n": 0}

    def counting(command):
        calls["n"] += 1
        return MEASURED_VERSION

    monkeypatch.setattr(copilot, "resolve_executable", lambda: "copilot.exe")
    monkeypatch.setattr(copilot, "_executable_stamp", lambda command: command)
    monkeypatch.setattr(copilot, "_probe_cli_version", counting)

    copilot.runtime_capabilities
    copilot.runtime_capabilities

    assert calls["n"] == 1


def test_a_replaced_binary_is_probed_again(copilot, monkeypatch):
    calls = {"n": 0}
    stamp = {"value": "first"}

    def counting(command):
        calls["n"] += 1
        return MEASURED_VERSION

    monkeypatch.setattr(copilot, "resolve_executable", lambda: "copilot.exe")
    monkeypatch.setattr(copilot, "_executable_stamp", lambda command: stamp["value"])
    monkeypatch.setattr(copilot, "_probe_cli_version", counting)

    copilot.runtime_capabilities
    stamp["value"] = "second"
    copilot.runtime_capabilities

    assert calls["n"] == 2


def test_a_failing_probe_command_scopes_nothing(copilot, monkeypatch):
    monkeypatch.setattr(copilot, "resolve_executable", lambda: "copilot.exe")
    monkeypatch.setattr(copilot, "_executable_stamp", lambda command: command)
    monkeypatch.setattr(
        copilot_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="", stderr="not logged in"
        ),
    )

    assert copilot.runtime_capabilities.path_scopable_tools == frozenset()


def test_the_probe_reads_the_version_from_the_cli(copilot, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args, 0, stdout=f"{MEASURED_VERSION}\n", stderr=""
        )

    monkeypatch.setattr(copilot_module.subprocess, "run", fake_run)

    assert copilot._probe_cli_version("copilot.exe") == MEASURED_VERSION
    assert seen["args"] == ["copilot.exe", "--version"]
    # The probe runs on the request path, so it must never be able to sit
    # waiting: closed stdin so it cannot prompt, a bounded timeout, and no
    # console window to steal focus on Windows.
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert seen["kwargs"]["timeout"] == CopilotIntegration._VERSION_PROBE_TIMEOUT
    assert seen["kwargs"]["timeout"] <= 5
    assert seen["kwargs"]["creationflags"] == getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )


def test_shell_is_never_claimed_as_path_scopable(copilot, monkeypatch):
    # The container backend is unavailable on this platform, so a shell claim
    # would be a promise the sandbox cannot keep.
    stub_version(monkeypatch, copilot, MEASURED_VERSION)

    assert "shell" not in copilot.runtime_capabilities.path_scopable_tools
    assert "shell" not in copilot.declared_runtime_capabilities.path_scopable_tools
