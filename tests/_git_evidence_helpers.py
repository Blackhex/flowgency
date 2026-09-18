"""Isolated on-disk Git fixtures for evidence-capture tests.

Test-only. These helpers write and commit inside ``tmp_path`` repositories so
capture behaviour can be proven against real Git output. They never touch the
project repository, and they pin every ambient Git setting that could change a
fixture's bytes: signing, hooks, templates, attributes, filters, and CRLF.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

GIT_EXECUTABLE = shutil.which("git")
requires_git = pytest.mark.skipif(GIT_EXECUTABLE is None, reason="git executable is unavailable")

_FIXTURE_IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


@dataclass(frozen=True)
class GitTestRepository:
    root: Path
    base_commit: str
    end_commit: str


def git_environment(root: Path) -> dict[str, str]:
    """Deterministic environment: no inherited GIT_*, no global/system config."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    absent = str(Path(root).parent / "absent-gitconfig")
    env.update(_FIXTURE_IDENTITY)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": absent,
            "GIT_CONFIG_SYSTEM": absent,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return env


def git_command(root: Path, *arguments: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=git_environment(root),
    )
    return result.stdout


def init_git_repository(root: Path, *, object_format: str = "sha1") -> None:
    root.mkdir(parents=True, exist_ok=True)
    git_command(
        root,
        "init",
        "--template=",
        "--initial-branch=main",
        f"--object-format={object_format}",
    )
    git_command(root, "config", "user.name", "Fixture Author")
    git_command(root, "config", "user.email", "fixture@example.invalid")
    git_command(root, "config", "core.autocrlf", "false")
    git_command(root, "config", "core.safecrlf", "false")
    git_command(root, "config", "commit.gpgsign", "false")
    git_command(root, "config", "core.hooksPath", str(root.parent / "absent-hooks"))


def create_git_repository(root: Path, *, object_format: str = "sha1") -> GitTestRepository:
    init_git_repository(root, object_format=object_format)
    (root / "result.txt").write_bytes(b"before\n")
    (root / "unrelated.txt").write_bytes(b"original\n")
    git_command(root, "add", "--", "result.txt", "unrelated.txt")
    git_command(root, "commit", "-m", "test: seed repository")
    base_commit = git_command(root, "rev-parse", "HEAD").decode().strip()
    (root / "result.txt").write_bytes(b"after\n")
    git_command(root, "add", "--", "result.txt")
    git_command(root, "commit", "-m", "test: publish result")
    end_commit = git_command(root, "rev-parse", "HEAD").decode().strip()
    return GitTestRepository(root, base_commit, end_commit)


def commit_tree(root: Path, entries: bytes, *, parent: str, message: str = "test: raw tree") -> str:
    """Commit a literal ``mktree -z`` payload.

    Needed for content the working tree cannot hold portably: symlink and
    gitlink modes, and paths whose bytes are not valid UTF-8.
    """
    tree = git_command(root, "mktree", "--missing", "-z", stdin=entries).decode().strip()
    commit = (
        git_command(root, "commit-tree", tree, "-p", parent, "-m", message).decode().strip()
    )
    return commit


def tree_entry(mode: str, kind: str, object_id: str, path: bytes) -> bytes:
    return f"{mode} {kind} {object_id}\t".encode() + path + b"\x00"


def write_blob(root: Path, content: bytes) -> str:
    return git_command(root, "hash-object", "-w", "--stdin", stdin=content).decode().strip()


def read_tree_entries(root: Path, commit: str) -> bytes:
    return git_command(root, "ls-tree", "-z", commit)


def sha256_supported(tmp_path: Path) -> bool:
    probe = tmp_path / "sha256-probe"
    try:
        init_git_repository(probe, object_format="sha256")
    except subprocess.CalledProcessError:
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    return True


def capture_request(fixture: GitTestRepository, **overrides):
    """The ordinary evidence request for a fixture's seeded commit range."""
    from flowgency.tickets.git_evidence import GitCaptureRequest

    payload = {
        "transition_id": "complete",
        "field_id": "evidence",
        "base_commit": fixture.base_commit,
        "end_commit": fixture.end_commit,
    }
    payload.update(overrides)
    return GitCaptureRequest(**payload)


def launch_policy_for(env, agent_name: str):
    """The launch-time snapshot a real launcher would have taken for an agent."""
    from flowgency.configuration.effective import resolve_effective_policy
    from flowgency.jobs.models import RuntimePolicySnapshot

    return RuntimePolicySnapshot.from_effective_policy(
        resolve_effective_policy(env.store.load().config, env.team_id, agent_name)
    )


def configure_git_ticket(env, fixture: GitTestRepository, policy, *, launch_policy=None):
    """Point a real team at a Git fixture and start a real run on a ticket.

    ``launch_policy`` overrides the snapshot the run was launched with, so a
    denied launch-time policy can be exercised. Returns the running agent's
    context and the started ticket view.
    """
    from flowgency.tickets.access import TicketAccessRegistry
    from flowgency.workflows.models import WorkflowDefinition

    snapshot = env.store.load()

    def configure(raw):
        team = raw["teams"][env.team_id]
        team["workspace_path"] = str(fixture.root)
        team["git_publication"] = policy.model_dump(mode="json")

    env.store.patch(snapshot.revision, configure)
    env.publish_artifact_field_workflow()
    source = env.library.inspect(env.blueprint_id)
    document = source.definition.model_dump(mode="json")
    for field in document["fields"]:
        if field["id"] == "evidence":
            field["artifact_format"] = "git-change"
    for transition in document["transitions"]:
        if transition["id"] == "complete":
            for use in transition["outputs"]:
                if use["field_id"] == "evidence":
                    use["required"] = True
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        WorkflowDefinition.model_validate(document),
    )
    authority = env.running_job(
        "builder",
        "git-evidence-run",
        runtime_policy=launch_policy or launch_policy_for(env, "builder"),
    )
    registry = TicketAccessRegistry(env.job_store)
    actor = registry.open(authority).context
    env.service.validate_agent_context = registry.validate_context
    env.service.resolve_git_job = registry.resolve_context
    ticket = env.create(values={"verdict": True, "summary": "Pending"})
    env.service.start_work(
        actor, ticket.version, env.operation("start", actor_name="builder")
    )
    return actor, env.read(ticket.ref)


def snapshot_repository(root: Path) -> dict[str, str]:
    """Hash every source ref, index byte, and worktree file for parity checks."""
    snapshot: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[f"dir:{relative}"] = "dir"
            continue
        snapshot[f"file:{relative}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    git_dir = Path(root) / ".git"
    if git_dir.is_file():
        git_dir = Path((git_dir.read_text(encoding="utf-8").split(":", 1)[1]).strip())
    for name in ("HEAD", "index", "packed-refs", "shallow"):
        candidate = git_dir / name
        snapshot[f"git:{name}"] = (
            hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else "absent"
        )
    snapshot["git:refs"] = git_command(
        root, "for-each-ref", "--format=%(refname) %(objectname)"
    ).decode()
    snapshot["git:status"] = git_command(root, "status", "--porcelain=v1", "-z").decode()
    return snapshot
