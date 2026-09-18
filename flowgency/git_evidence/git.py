"""Bounded, isolated Git reads for evidence capture.

Everything here is a *read* boundary. A capture must never be able to write to
the source repository, follow a repository-local Git executable, inherit
ambient Git configuration, run a hook or diff helper, or spend unbounded time
or memory. So the source repository is validated by hand, never used as a
process working directory, and only its object database is exposed – through a
disposable Git directory Flowgency owns, under a scratch root, with an explicit
alternate. Ordinary capture commands run against that private view.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from flowgency.git_evidence.models import GitEvidenceError, GitRepository
from flowgency.jobs.processes import RuntimeProcessLifecycle, run_supervised

GIT_COMMAND_TIMEOUT_SECONDS = 30
CAPTURE_TIMEOUT_SECONDS = 120
METADATA_OUTPUT_LIMIT_BYTES = 1024 * 1024
MAX_PATCH_BYTES = 640 * 1024
# Diagnostics Git writes alongside a read (rename-limit or advice warnings) get
# their own bounded room, so a patch that exactly fills its byte budget is not
# rejected because of a harmless warning.
STDERR_ALLOWANCE_BYTES = 64 * 1024

_SUPPORTED_OBJECT_FORMATS = {"sha1", "sha256"}

# Only these inherited names survive into a Git subprocess. An allowlist is the
# only way to be sure no credential, tracing, pager, SSH, or GIT_CONFIG_*
# injection variable reaches Git, since new ones are added over time.
_INHERITED_ENV_NAMES = (
    "SystemRoot",
    "windir",
    "SystemDrive",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _within_any(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(_within(path, root) for root in roots)


def _deployment_path_entries(forbidden_roots: tuple[Path, ...]) -> list[Path]:
    """Absolute, existing PATH directories outside the untrusted roots.

    A relative entry resolves against whatever the working directory happens to
    be, and an entry under the workspace or scratch root is attacker-writable,
    so a repository-local ``git.exe`` could otherwise become the trusted tool.
    """
    entries: list[Path] = []
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_dir() or _within_any(resolved, forbidden_roots):
            continue
        entries.append(resolved)
    return entries


def _executable_suffixes() -> list[str]:
    if os.name != "nt":
        return [""]
    raw = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    return [suffix for suffix in raw.split(os.pathsep) if suffix]


def resolve_trusted_executable(name: str, *, forbidden_roots: tuple[Path, ...]) -> Path:
    """Find ``name`` on the sanitized deployment path.

    ``shutil.which`` prepends the current directory on Windows, which is the
    exact lookup this boundary must not perform, so the search is explicit.
    """
    suffixes = _executable_suffixes()
    for directory in _deployment_path_entries(forbidden_roots):
        for suffix in suffixes:
            candidate = directory / f"{name}{suffix}"
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=False)
            if _within_any(resolved, forbidden_roots):
                continue
            return resolved
    raise GitEvidenceError("git-evidence-git-unavailable")


def _untrusted_roots(workspace: Path, session: Path) -> tuple[Path, ...]:
    """Roots no trusted executable may come from: the source workspace, this
    capture's private session, and the whole scratch root that contains it."""
    return (workspace, session, session.parent)


def _git_environment(session: Path, *, forbidden_roots: tuple[Path, ...]) -> dict[str, str]:
    home = session / "home"
    temp = session / "temp"
    env = {
        name: os.environ[name] for name in _INHERITED_ENV_NAMES if name in os.environ
    }
    # Git searches this PATH for its own helpers, so it must exclude exactly the
    # roots the executable lookup refuses, not only the private session.
    env["PATH"] = os.pathsep.join(
        str(entry) for entry in _deployment_path_entries(forbidden_roots)
    )
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / "config")
    env["TMP"] = str(temp)
    env["TEMP"] = str(temp)
    env["TMPDIR"] = str(temp)
    env["LC_ALL"] = "C"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_ATTR_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _run_git_raw(
    executable: Path,
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
    output_limit: int,
) -> tuple[int, bytes]:
    remaining = min(GIT_COMMAND_TIMEOUT_SECONDS, deadline - time.monotonic())
    if remaining <= 0:
        raise GitEvidenceError("git-evidence-timeout")
    argv = [
        str(executable),
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=",
        *arguments,
    ]
    try:
        result = run_supervised(
            argv,
            cwd=cwd,
            env=env,
            timeout=max(1, math.ceil(remaining)),
            lifecycle=lifecycle,
            output_limit_bytes=output_limit + STDERR_ALLOWANCE_BYTES,
            retain_output_bytes=True,
        )
    except FileNotFoundError:
        raise GitEvidenceError("git-evidence-git-unavailable") from None
    if result.output_limit_exceeded:
        raise GitEvidenceError("git-evidence-output-too-large")
    if result.outcome == "timeout":
        raise GitEvidenceError("git-evidence-timeout")
    if result.outcome != "exited":
        raise GitEvidenceError("git-evidence-command-failed")
    stdout = result.stdout_bytes or b""
    # The supervisor's cap is deliberately the combined one; each stream is then
    # held to its own share so neither can spend the other's budget.
    if len(stdout) > output_limit or len(result.stderr_bytes or b"") > STDERR_ALLOWANCE_BYTES:
        raise GitEvidenceError("git-evidence-output-too-large")
    return result.exit_code, stdout


def _validated_workspace(workspace: Path) -> Path:
    candidate = Path(workspace)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise GitEvidenceError("git-evidence-workspace-invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise GitEvidenceError("git-evidence-workspace-invalid") from None
    if not resolved.is_dir():
        raise GitEvidenceError("git-evidence-workspace-invalid")
    return resolved


def _linked_worktree_directories(workspace: Path, pointer: Path) -> tuple[Path, Path]:
    """Accept a ``.git`` pointer only if the repository registered it.

    A caller-supplied pointer is not authority: the admin directory must point
    back at this workspace, and the common directory must list it as one of its
    own worktrees.
    """
    text = pointer.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        raise GitEvidenceError("git-evidence-not-a-repository")
    target = Path(text[len("gitdir:") :].strip())
    if not target.is_absolute():
        target = workspace / target
    try:
        admin = target.resolve(strict=True)
    except OSError:
        raise GitEvidenceError("git-evidence-worktree-unregistered") from None

    back_pointer = admin / "gitdir"
    common_file = admin / "commondir"
    if not back_pointer.is_file() or not common_file.is_file():
        raise GitEvidenceError("git-evidence-worktree-unregistered")
    registered_pointer = Path(back_pointer.read_text(encoding="utf-8").strip())
    if registered_pointer.resolve(strict=False) != pointer.resolve(strict=False):
        raise GitEvidenceError("git-evidence-worktree-unregistered")

    common_raw = Path(common_file.read_text(encoding="utf-8").strip())
    common = common_raw if common_raw.is_absolute() else admin / common_raw
    try:
        common = common.resolve(strict=True)
    except OSError:
        raise GitEvidenceError("git-evidence-worktree-unregistered") from None
    if (common / "worktrees" / admin.name).resolve(strict=False) != admin:
        raise GitEvidenceError("git-evidence-worktree-unregistered")
    return admin, common


def _resolve_git_directories(workspace: Path) -> tuple[Path, Path]:
    pointer = workspace / ".git"
    if pointer.is_dir():
        git_dir = pointer.resolve(strict=True)
        if not _within(git_dir, workspace):
            raise GitEvidenceError("git-evidence-workspace-invalid")
        return git_dir, git_dir
    if pointer.is_file():
        return _linked_worktree_directories(workspace, pointer)
    if (
        (workspace / "HEAD").is_file()
        and (workspace / "objects").is_dir()
        and (workspace / "refs").is_dir()
    ):
        raise GitEvidenceError("git-evidence-bare-repository")
    raise GitEvidenceError("git-evidence-not-a-repository")


def _reject_untrusted_object_sources(common_dir: Path) -> None:
    if (common_dir / "objects" / "info" / "alternates").exists():
        raise GitEvidenceError("git-evidence-unsafe-repository")
    if (common_dir / "info" / "grafts").exists():
        raise GitEvidenceError("git-evidence-unsafe-repository")
    replace_dir = common_dir / "refs" / "replace"
    if replace_dir.is_dir() and any(replace_dir.iterdir()):
        raise GitEvidenceError("git-evidence-unsafe-repository")
    packed_refs = common_dir / "packed-refs"
    if packed_refs.is_file() and b"refs/replace/" in packed_refs.read_bytes():
        raise GitEvidenceError("git-evidence-unsafe-repository")
    if (common_dir / "shallow").exists():
        raise GitEvidenceError("git-evidence-shallow-repository")


def _repository_identity(common_dir: Path, object_format: str) -> str:
    try:
        stat = common_dir.stat()
    except OSError:
        raise GitEvidenceError("git-evidence-repository-changed") from None
    payload = "\0".join(
        [str(common_dir), object_format, str(stat.st_dev), str(stat.st_ino)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_repository_identity(repository: GitRepository) -> None:
    """Fail rather than bind to a different repository swapped in mid-capture."""
    try:
        source_git_dir, common_dir = _resolve_git_directories(repository.workspace)
    except GitEvidenceError:
        raise GitEvidenceError("git-evidence-repository-changed") from None
    if source_git_dir != repository.source_git_dir or common_dir != repository.common_dir:
        raise GitEvidenceError("git-evidence-repository-changed")
    if _repository_identity(common_dir, repository.object_format) != repository.repository_id:
        raise GitEvidenceError("git-evidence-repository-changed")


def _source_config_value(
    key: str,
    *,
    common_dir: Path,
    executable: Path,
    session: Path,
    env: dict[str, str],
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> str | None:
    """Read one source config value with includes disabled and no discovery."""
    code, data = _run_git_raw(
        executable,
        (
            "config",
            "--no-includes",
            "--file",
            str(common_dir / "config"),
            "--get",
            key,
        ),
        cwd=session,
        env=env,
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=64 * 1024,
    )
    if code != 0:
        return None
    return data.decode("utf-8", errors="replace").strip()


@contextlib.contextmanager
def open_git_repository(
    workspace: Path,
    *,
    scratch_root: Path,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float | None = None,
) -> Iterator[GitRepository]:
    effective_deadline = (
        time.monotonic() + CAPTURE_TIMEOUT_SECONDS if deadline is None else deadline
    )
    validated_workspace = _validated_workspace(workspace)
    scratch = Path(scratch_root)
    if not scratch.is_absolute() or ".." in scratch.parts:
        raise GitEvidenceError("git-evidence-scratch-invalid")
    if _within(scratch, validated_workspace):
        raise GitEvidenceError("git-evidence-scratch-invalid")

    source_git_dir, common_dir = _resolve_git_directories(validated_workspace)
    _reject_untrusted_object_sources(common_dir)

    session = scratch / f"git-evidence-{uuid.uuid4().hex}"
    object_view = session / "view.git"
    try:
        for directory in (session / "home" / "config", session / "temp"):
            directory.mkdir(parents=True, exist_ok=True)
        executable = resolve_trusted_executable(
            "git", forbidden_roots=_untrusted_roots(validated_workspace, session)
        )
        env = _git_environment(
            session, forbidden_roots=_untrusted_roots(validated_workspace, session)
        )

        if _source_config_value(
            "core.bare",
            common_dir=common_dir,
            executable=executable,
            session=session,
            env=env,
            lifecycle=lifecycle,
            deadline=effective_deadline,
        ) == "true":
            raise GitEvidenceError("git-evidence-bare-repository")
        object_format = (
            _source_config_value(
                "extensions.objectformat",
                common_dir=common_dir,
                executable=executable,
                session=session,
                env=env,
                lifecycle=lifecycle,
                deadline=effective_deadline,
            )
            or "sha1"
        )
        if object_format not in _SUPPORTED_OBJECT_FORMATS:
            raise GitEvidenceError("git-evidence-unsafe-repository")

        code, _ = _run_git_raw(
            executable,
            (
                "init",
                "--bare",
                "--quiet",
                "--template=",
                "--initial-branch=flowgency-evidence-view",
                f"--object-format={object_format}",
                str(object_view),
            ),
            cwd=session,
            env=env,
            lifecycle=lifecycle,
            deadline=effective_deadline,
            output_limit=64 * 1024,
        )
        if code != 0:
            raise GitEvidenceError("git-evidence-command-failed")

        alternates = object_view / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        # Written as bytes: Git keeps a CRLF terminator as part of the path.
        alternates.write_bytes(f"{(common_dir / 'objects').as_posix()}\n".encode())

        yield GitRepository(
            workspace=validated_workspace,
            source_git_dir=source_git_dir,
            common_dir=common_dir,
            object_view=object_view,
            object_format=object_format,
            repository_id=_repository_identity(common_dir, object_format),
        )
    finally:
        shutil.rmtree(session, ignore_errors=True)


def run_git_exit_code(
    repository: GitRepository,
    arguments: tuple[str, ...],
    *,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
    output_limit: int,
) -> tuple[int, bytes]:
    session = repository.object_view.parent
    forbidden_roots = _untrusted_roots(repository.workspace, session)
    executable = resolve_trusted_executable("git", forbidden_roots=forbidden_roots)
    return _run_git_raw(
        executable,
        ("--git-dir", str(repository.object_view), *arguments),
        cwd=repository.object_view,
        env=_git_environment(session, forbidden_roots=forbidden_roots),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=output_limit,
    )


def run_git_bytes(
    repository: GitRepository,
    arguments: tuple[str, ...],
    *,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
    output_limit: int,
) -> bytes:
    code, data = run_git_exit_code(
        repository,
        arguments,
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=output_limit,
    )
    if code != 0:
        raise GitEvidenceError("git-evidence-command-failed")
    return data
