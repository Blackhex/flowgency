# Workflow Git Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any workflow require immutable evidence of locally committed Git changes, checked against optional local-ref restrictions, and expose the retained patch through a read-only line-by-line viewer.

**Architecture:** Add an optional artifact format to generic field definitions and an explicit team/workspace local commit/ref policy to canonical configuration. A trusted capture operation resolves job authority, produces a bounded immutable artifact from explicit commit endpoints, and records a capture audit receipt; transitions accept that artifact only after validating its receipt and current policy. Render retained bytes and provenance, never a diff recomputed from today's workspace. No remote-publication verification or credential handling is part of capture.

**Tech Stack:** Python >=3.11, Git CLI, Pydantic >=2.8,<3, FastAPI/Jinja2, existing Local ticket storage, `unidiff>=0.7.5,<0.8` for unified-diff parsing, pytest, Playwright, and existing vanilla JavaScript.

## Global Constraints

- "Staged, unstaged, and untracked content never enters the artifact."
- "Selecting a semantically appropriate range remains the submitting agent's responsibility; do not claim automatic separation of unrelated commits inside an explicitly selected range."
- "Capture proves the selected Git content and its association with the submitting agent, not exclusive human or agent authorship of every included commit."
- "Runtime does not parse prose instructions or infer a requirement from the presence of an `origin` remote."
- "If no applicable policy is configured, a Git-evidence request fails with an actionable configuration error; ordinary workflows remain unaffected."
- "Flowgency verifies committed content locally only."
- "Do not contact remotes, verify pushes, fetch missing objects, select SSH agents, invoke credential helpers, or add authentication/remote-endpoint configuration for evidence capture."
- "A workflow blueprint must work for both local-only and remotely published projects without hard-coded remote names or branches."
- "Do not manufacture evidence by committing, rebasing, merging, or pushing on the agent's behalf."
- "No PR integration, patch application, or code editing is part of the read-only viewer."
- "Do not duplicate the patch into job records, logs, or field text."
- "Capture must also respect the actor's effective read permissions and workspace boundary for selected source paths."
- "Reject a disallowed range rather than silently producing a partial diff."
- "Git commits and ticket persistence are separate operations."
- "Existing definitions and ordinary artifact fields retain their behavior without conversion."
- Retain `MAX_RETAINED_ARTIFACT_BYTES = 1 * 1024 * 1024`; the complete evidence envelope must fit this existing cap. Do not raise limits for all ordinary uploads.
- No live configuration edits, ticket backfills, old-job reconstruction, real agent launches, automatic permission grants, or modifications to `build/lib`.
- Follow the worktree, baseline, review, and integration rules in the foundation plan and `AGENTS.md`. Do not implement on `master`.

---

## Prerequisite and Scope

Source specification: `docs/superpowers/specs/2026-09-18-workflow-transition-outputs-design.md`, revised in `e6e52e9` and approved by the user's writing-plans request.

Scope revision approved on 2026-09-18 and recorded in specification commit
`c816124`: "Do not validate the remote publication." Tasks 1-2 are already
complete. Task 3 removes the unshipped remote verifier and remote-only policy
introduced before this ruling, then completes local verification. Tasks 4-7 use
only the revised contracts below. Historical task reports remain evidence of
what ran, not authority to restore remote checks. Project instructions may still
require an agent to push; capture neither performs nor verifies that action.

Execute `docs/superpowers/plans/2026-09-18-workflow-transition-outputs.md` first. Its projection and generic authoring contract are prerequisites; its full-suite baseline and review records remain applicable if execution is continuous. If execution resumes after branch changes, rerun the baseline before making changes.

Foundation: [2026-09-18-workflow-transition-outputs.md](2026-09-18-workflow-transition-outputs.md).

No mockup has been approved and no normative asset directory exists. This plan specifies a conventional unified-diff page using the application's existing shell, typography, colors, focus styles, and icon library. Do not introduce a new visual system. Any subsequently approved mockup must be archived as HTML and PNG and referenced here before its implementation.

## File Structure and Ownership

| Files | Responsibility |
| --- | --- |
| New `flowgency/git_evidence/__init__.py` | Package marker only; no startup work |
| New `flowgency/git_evidence/models.py` | Pure policy, range, evidence, receipt, and limit types |
| New `flowgency/git_evidence/git.py` | Bounded Git subprocess execution, repository identity, and safe NUL-delimited parsing |
| New `flowgency/git_evidence/capture.py` | Committed range selection, read-permission checks, manifest and patch construction |
| New `flowgency/git_evidence/publication.py` | Local commit/ref policy verification in disposable metadata storage |
| New `flowgency/tickets/git_evidence.py` | Capture receipts and accepted-artifact verification at the ticket boundary |
| `flowgency/configuration/models.py`, `flowgency/configuration/patches.py` | Canonical optional policy and revision-checked policy patch |
| `flowgency/workflows/models.py`, `forms.py`, `editing.py`, `configuration.py` | Optional artifact format, authoring round trips, policy/workspace context fence |
| `flowgency/tickets/service.py`, `access.py`, `protocol.py`, `mcp_server.py` | Trusted capture orchestration, job authority, typed authenticated tool, transition checks |
| `flowgency/web/dependencies.py`, `flowgency/jobs/tickets.py` | Dependency injection using the existing job store and live registry |
| `flowgency/jobs/processes.py`, `flowgency/jobs/execution.py` | Opt-in bounded raw-byte supervision and worker capture dependency wiring |
| `flowgency/tickets/views.py` | Artifact-format and verified evidence links in shared ticket views |
| New `flowgency/web/git_evidence.py` | Read-only evidence/view-model lookup and bounded parsed diff projection |
| New `flowgency/web/routes/git_evidence.py` | Ticket-scoped viewer and exact patch download routes |
| New `flowgency/templates/git_evidence.html`, `flowgency/static/git-evidence.css` | Accessible unified diff presentation |
| `flowgency/templates/_ticket_inspector.html`, `flowgency/templates/job_detail.html` | Links from outputs, historical outputs, and exact producing jobs |
| `flowgency/static/workflow-editor.js`, `flowgency/templates/workflow_blueprint.html` | Artifact format selection within existing controls |
| `pyproject.toml`, `kb/data-formats.md`, `kb/configuration.md`, setup skill sources | Parser dependency and user/agent authoring contract |
| New `tests/test_git_evidence.py` and `tests/_git_evidence_helpers.py` | Isolated Git fixtures, range/permission/local-ref tests |
| Existing config/workflow/ticket tests; new `tests/ui/git_evidence.spec.ts` | Authority, persistence, protocol, UI, and packaging gates |

Keep Git and ticket service code separated by typed inputs/results. Do not put subprocess or network work into Pydantic validators, Jinja templates, or `evaluate_transition`. Do not change the Local storage port: existing `put_artifact`, `read_artifact`, `apply`, and operation receipts are sufficient.

## Contract Choices

These are concrete implementation choices within the approved design:

1. `FieldDefinition.artifact_format: Literal["git-change"] | None = None`. Only `type="artifact"` may set it. An omitted format is an ordinary artifact.
2. `TeamConfig.git_publication: GitPublicationPolicy | None = None`. It describes the existing `workspace_path`, not a second workspace or auto-discovered Git root.
3. The only publication mode is `local`. Allowed local-ref restrictions are exact `refs/heads/...` / `refs/tags/...` values or a single trailing `/*` prefix pattern; empty restrictions are allowed.
4. `GitPublicationPolicy` contains only `mode: Literal["local"]` and `allowed_refs: tuple[StrictStr, ...] = ()`, with `extra="forbid"`. Reject remote mode and remote/authentication fields; do not silently discard them or retain a dormant transport branch.
5. Evidence capture never discovers or contacts a remote, invokes credential helpers or SSH, reads authentication material, or grants credentials. Project push obligations stay in project instructions. Retain existing runtime credential-withholding behavior unchanged for other operations.
6. Capture accepts full lowercase hexadecimal Git object IDs of one repository's object format, not revision expressions. Git resolves them to commit objects and verifies ancestry. `publication_ref` is optional only for an unrestricted local policy; otherwise it must match the configured allowlist.
7. Use a canonical JSON manifest as the retained artifact's content, with the exact patch encoded as strict base64. Media type: `application/vnd.flowgency.git-change+json`. Download decodes the exact patch as `application/octet-stream` with an attachment filename ending `.patch`.
8. Trust is established by a `git-evidence-captured` audit event written only by the capture service. Media type, extension, caller-provided JSON, and field names never establish trust.
9. Capture increments ticket revision and records an operation receipt but changes no state, field, assignment, or active-run ownership. Agents use the returned current ticket version for the subsequent transition.
10. Working limits: 512 commits, 1,024 changed paths, 30 seconds per Git subprocess, 120 seconds total per capture, 640 KiB of patch bytes, and 1 MiB for the complete retained manifest. Preview at most 200 KiB of patch data and 2,000 diff lines; provide the entire retained patch download. Limit failures are explicit, never silently incomplete evidence.
11. Local-ref verification retains the observed ref object and peeled commit IDs. If the local graph cannot prove ancestry because objects are missing, return `git-evidence-verification-incomplete`. Never fetch. `GitPublicationReceipt.mode` is `Literal["local"]` and it has no remote identity. The viewer labels the evidence `Local commits` and never claims a verified push.

### Task 1: Add the Generic Artifact Format and Project Policy

**Files:**
- Create: `flowgency/git_evidence/__init__.py`, `flowgency/git_evidence/models.py`.
- Modify: `flowgency/workflows/models.py`, `flowgency/workflows/forms.py`, `flowgency/workflows/editing.py`.
- Modify: `flowgency/configuration/models.py`, `flowgency/configuration/patches.py`, `flowgency/workflows/configuration.py`.
- Test: `tests/test_workflow_contracts.py`, `tests/test_workflow_forms.py`, `tests/test_workflow_editing.py`, `tests/test_workflow_configuration.py`, `tests/test_config_patches.py`.
- Create/Test: `tests/test_git_evidence.py` (pure policy tests first; later tasks extend it).

**Interfaces:**
- Consumes: `FieldDefinition`, `DraftField`, `TeamConfig`, `ConfigStore.patch`, `ConfigSnapshot`, and `WorkflowBinding`.
- Produces: `GitPublicationPolicy`, `git_policy_digest(policy: GitPublicationPolicy | None) -> str | None` in `flowgency/git_evidence/models.py`.
- Produces: `patch_team_git_publication(store: ConfigStore, expected_revision: str, team_id: str, policy: GitPublicationPolicy | None) -> ConfigSnapshot` in `configuration/patches.py`.
- Produces: optional `artifact_format` on field models and editor drafts, and optional `git_publication` on the parsed team model. Missing policy remains missing.

- [ ] **Step 1: Add failing field-contract tests.**

```python
@pytest.mark.parametrize("kind", ["text", "number", "boolean"])
def test_git_change_format_requires_artifact_field(kind):
    from pydantic import ValidationError
    from flowgency.workflows.models import FieldDefinition

    with pytest.raises(ValidationError):
        FieldDefinition(id="result", label="Result", type=kind, artifact_format="git-change")


def test_git_change_format_round_trips_without_changing_ordinary_artifacts():
    from flowgency.workflows.models import FieldDefinition

    result = FieldDefinition(
        id="result", label="Result", type="artifact", artifact_format="git-change"
    )
    assert FieldDefinition.model_validate(result.model_dump()).artifact_format == "git-change"
    ordinary = FieldDefinition(id="file", label="File", type="artifact")
    assert ordinary.artifact_format is None
```

Run: `python -m pytest tests/test_workflow_contracts.py -k git_change_format -q`.
Expected: the valid Git-format construction fails before implementation; forbidden combinations remain rejected.

- [ ] **Step 2: Add the optional format and preserve it through every existing editing conversion.**

```python
artifact_format: Literal["git-change"] | None = None

@model_validator(mode="after")
def _validate_artifact_format(self) -> "FieldDefinition":
    if self.artifact_format is not None and self.type != "artifact":
        raise ValueError("Artifact format requires an artifact field")
    return self
```

Add the corresponding `DraftField` property; carry it through draft creation, `parse_editor_draft`, and `FieldDefinition(...)` reconstruction. Extend `add_field(definition, label, kind, *, artifact_format=None)` with the same optional keyword so existing callers are unchanged. Preserve format when fields are renamed/reused. Do not infer it from content or media type. Rerun the same contract test immediately.

The library hashes source bytes, so reads must not rewrite existing definitions. Also omit an unset `artifact_format` from canonical field serialization to avoid adding null keys during an otherwise ordinary save. Use a wrapping Pydantic `model_serializer` on `FieldDefinition` that calls the supplied handler, removes only `artifact_format` when it is `None`, and preserves every other existing field. Do not use a broad `exclude_none=True` on the entire workflow. Add `assert "artifact_format" not in ordinary.model_dump()` to the ordinary-field test and verify existing source digest tests stay unchanged.

```python
@model_serializer(mode="wrap")
def _serialize_field(self, handler):
    payload = handler(self)
    if self.artifact_format is None:
        payload.pop("artifact_format", None)
    return payload
```

Import `model_serializer` from Pydantic in the owning file; this method belongs to `FieldDefinition`, not `WorkflowDefinition`.

- [ ] **Step 3: Define and test explicit publication policies.** Add pure models with these exact fields:

```python
class GitPublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["local"]
    allowed_refs: tuple[StrictStr, ...] = ()
```

Reject duplicate refs. Validate full ref syntax, rejecting controls, backslash, `..`, `@{`, `:`, wildcard except final `/*`, empty components, `.lock` suffixes, and malformed `refs/heads/` or `refs/tags/` names. Remote mode and every additional remote/authentication field are invalid. Do not add URL, known-hosts, SSH-agent, or credential-manager validators.

Model construction performs no IO. Canonical digest uses `json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))` and SHA-256; `None` returns `None`. Task 3 removes remote-only normalization/path validators created under the superseded contract; ordinary path validation remains unchanged.

```python
def test_local_git_policy_needs_no_remote():
    from flowgency.git_evidence.models import GitPublicationPolicy

    policy = GitPublicationPolicy(mode="local")
    assert policy.allowed_refs == ()


def test_remote_git_policy_is_not_supported():
    from pydantic import ValidationError
    from flowgency.git_evidence.models import GitPublicationPolicy

    with pytest.raises(ValidationError):
        GitPublicationPolicy.model_validate({
            "mode": "remote", "allowed_refs": ["refs/heads/main"],
            "remote": {"name": "origin", "url": "https://example.invalid/project.git"},
        })
```

    Place these pure tests in the new `tests/test_git_evidence.py` file (created with this task). Run `python -m pytest tests/test_git_evidence.py -k policy -q` after adding each validation group. Include table-driven invalid refs/unsupported fields and an ordinary team whose policy remains `None`.

- [ ] **Step 4: Add a revision-checked canonical policy patch and context fence.**

```python
def patch_team_git_publication(store, expected_revision, team_id, policy):
    def apply(raw):
        team = raw["teams"][team_id]
        if policy is None:
            team.pop("git_publication", None)
        else:
            team["git_publication"] = policy.model_dump(mode="json")
    return store.patch(expected_revision, apply)
```

Use the typed public signature listed above. Add the optional policy field to `TeamConfig` and ensure the canonical parsing/normalization pipeline preserves it. Reuse `ConfigStore` locks, full validation, unrelated-data preservation, and atomic replacement. Do not add a standalone policy file.

Extend `_context_digest` in `workflows/configuration.py` to include normalized `workspace_path` and `git_policy_digest` when a policy exists. Preserve the old digest payload when no policy is configured, so unrelated workflows do not acquire stale-context failures solely from this additive release. `binding_id` remains storage identity and never includes policy. Removing an existing policy still changes context because the prior digest included it.

```python
def test_git_policy_changes_context_not_storage_identity(configured_store):
    from flowgency.configuration.patches import patch_team_git_publication
    from flowgency.git_evidence.models import GitPublicationPolicy
    from flowgency.workflows.configuration import resolve_workflow_binding

    before_snapshot = configured_store.load()
    before = resolve_workflow_binding(before_snapshot, "newsletter", "workflow-one")
    after_snapshot = patch_team_git_publication(
        configured_store, before_snapshot.revision, "newsletter",
        GitPublicationPolicy(mode="local"),
    )
    after = resolve_workflow_binding(after_snapshot, "newsletter", "workflow-one")
    assert before.storage.binding_id == after.storage.binding_id
    assert before.context_digest != after.context_digest
    assert before_snapshot.raw["teams"]["newsletter"]["agents"] == after_snapshot.raw["teams"]["newsletter"]["agents"]
```

Put this in `tests/test_workflow_configuration.py`, which already owns `configured_store`. Add stale-revision, unchanged-policy, policy-removal, and different-workspace cases using the same fixture. A stale patch must leave the file byte-identical.

- [ ] **Step 5: Run the contract slice, commit, and review before capture work.**

Run: `python -m pytest tests/test_git_evidence.py tests/test_workflow_contracts.py tests/test_workflow_forms.py tests/test_workflow_editing.py tests/test_workflow_configuration.py tests/test_config_patches.py -q`.

```powershell
git add flowgency/git_evidence/__init__.py flowgency/git_evidence/models.py flowgency/workflows/models.py flowgency/workflows/forms.py flowgency/workflows/editing.py flowgency/workflows/configuration.py flowgency/configuration/models.py flowgency/configuration/patches.py tests/test_git_evidence.py tests/test_workflow_contracts.py tests/test_workflow_forms.py tests/test_workflow_editing.py tests/test_workflow_configuration.py tests/test_config_patches.py
git commit -m "feat(workflows): declare git evidence contracts"
```

Review: no default policy, no runtime inference from `origin`, no credential strings in snapshots, unchanged ordinary artifacts, and no live config edits. Constructor defaults must preserve existing source compatibility.

### Task 2: Capture Exact Committed Ranges With Bounded Git Reads

**Files:**
- Modify: `flowgency/git_evidence/models.py`.
- Create: `flowgency/git_evidence/git.py`, `flowgency/git_evidence/capture.py`, `tests/_git_evidence_helpers.py`.
- Modify: `flowgency/jobs/processes.py` (opt-in byte/output-limit options only).
- Test: `tests/test_git_evidence.py`, `tests/test_runtime_process_lifecycle.py`.

**Interfaces:**
- Consumes: `EffectiveRuntimePolicy.tools_for(path)`, `RuntimeProcessLifecycle`, existing process-tree supervision, and Task 1's models.
- Produces: frozen `GitRepository(workspace: Path, source_git_dir: Path, common_dir: Path, object_view: Path, object_format: str, repository_id: str)` and `GitCommitRange(base_commit: str, end_commit: str)`.
- Produces: `GitFileChange` with `path: str`, `old_path: str | None`, `status: Literal["added", "modified", "deleted", "renamed", "type-changed"]`, `lines_added: int`, `lines_removed: int`, `binary: bool`, `submodule: bool`, and old/new modes and object IDs as strings.
- Produces: frozen `GitRangeCapture(repository_id: str, base_commit: str, end_commit: str, commit_ids: tuple[str, ...], files: tuple[GitFileChange, ...], patch: bytes)`.
- Produces: `GitEvidenceError(code: str, message: str)` with fixed public codes/messages and no raw stderr/credentials.
- Produces: context manager `open_git_repository(workspace: Path, *, scratch_root: Path, lifecycle: RuntimeProcessLifecycle, deadline: float | None = None) -> Iterator[GitRepository]` in `git.py`; the service supplies its overall capture deadline, while isolated unit tests may use the bounded default.
- Produces: `run_git_bytes(repository: GitRepository, arguments: tuple[str, ...], *, lifecycle: RuntimeProcessLifecycle, deadline: float, output_limit: int) -> bytes` in `git.py`.
- Produces: `capture_committed_range(repository: GitRepository, selected: GitCommitRange, *, policies: tuple[EffectiveRuntimePolicy, ...], lifecycle: RuntimeProcessLifecycle, deadline: float) -> GitRangeCapture` in `capture.py`.
- Extends: `run_supervised` with optional `output_limit_bytes: int | None = None` and `retain_output_bytes: bool = False`; adds defaulted `stdout_bytes: bytes | None = None`, `stderr_bytes: bytes | None = None`, and `output_limit_exceeded: bool = False` to `CompletedRuntimeProcess`. Existing callers retain their behavior.

- [ ] **Step 1: Add raw-byte and output-limit supervisor regressions before using it for Git.**

In `tests/test_runtime_process_lifecycle.py`, reuse its existing lifecycle/environment fixtures and subprocess launch pattern. One child writes `bytes([0, 255, 10])` directly to stdout and exits. Another emits at least 2 MiB while the limit is 1,024 bytes and has a child process that would otherwise remain alive. Assert raw-byte equality, a limit flag, retained bytes no larger than the combined cap, and confirmed process-tree cleanup. Also assert a normal caller without the new options still gets the existing text output and outcome.

The key child commands are:

```python
binary_child = [sys.executable, "-c", "import os; os.write(1, bytes([0, 255, 10]))"]
oversized_child = [sys.executable, "-c", "import os; os.write(1, b'x' * (2 * 1024 * 1024))"]
```

Run: `python -m pytest tests/test_runtime_process_lifecycle.py -k "raw_bytes or output_limit" -q`.
Expected before implementation: unsupported keyword/options. Implement the optional buffer limits by sharing a byte budget across both existing reader threads, signaling the supervisor on overflow, and using its existing Windows Job Object/POSIX group cleanup path. Do not call `process.kill()` on only the root and leave children alive. Retain raw bytes only when requested; ordinary runtime capture stays unchanged. Rerun the same tests immediately, then the whole lifecycle file before the Git steps.

- [ ] **Step 2: Create one reusable isolated Git fixture helper and the committed-only regression.**

```python
from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class GitTestRepository:
    root: Path
    base_commit: str
    end_commit: str


def git_command(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout


def create_git_repository(root: Path) -> GitTestRepository:
    root.mkdir()
    git_command(root, "init", "--initial-branch=main")
    git_command(root, "config", "user.name", "Fixture Author")
    git_command(root, "config", "user.email", "fixture@example.invalid")
    git_command(root, "config", "core.autocrlf", "false")
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
```

This helper is test-only and may write/commit inside `tmp_path`. Add deterministic Git environment isolation to the helper so global signing, hooks, templates, filters, and CRLF settings cannot affect fixtures. Skip only when Git is genuinely unavailable; Git is required for the implementation gate.

```python
def test_capture_excludes_dirty_staged_and_untracked_content(tmp_path):
    import time
    from flowgency.git_evidence.capture import capture_committed_range
    from flowgency.git_evidence.git import open_git_repository
    from flowgency.git_evidence.models import GitCommitRange
    from flowgency.integrations.models import EffectiveRuntimePolicy
    from flowgency.jobs.processes import RuntimeProcessLifecycle
    from tests._git_evidence_helpers import create_git_repository, git_command

    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / "unrelated.txt").write_bytes(b"staged secret\n")
    git_command(fixture.root, "add", "--", "unrelated.txt")
    (fixture.root / "result.txt").write_bytes(b"uncommitted result\n")
    (fixture.root / "untracked.txt").write_bytes(b"untracked secret\n")
    lifecycle = RuntimeProcessLifecycle(job_id="capture-test", generation="generation-a")
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        captured = capture_committed_range(
            repository, GitCommitRange(fixture.base_commit, fixture.end_commit),
            policies=(EffectiveRuntimePolicy(timeout=30),),
            lifecycle=lifecycle, deadline=time.monotonic() + 120,
        )
    assert [change.path for change in captured.files] == ["result.txt"]
    assert b"+after" in captured.patch
    assert b"uncommitted" not in captured.patch
    assert b"secret" not in captured.patch
    assert captured.commit_ids == (fixture.end_commit,)
```

Run: `python -m pytest tests/test_git_evidence.py -k capture_excludes -q`.
Expected: import/signature failure before capture implementation, then the desired byte assertions become the discriminating check.

- [ ] **Step 3: Implement safe repository resolution and an isolated Git object view.**

Resolve the configured workspace only. Reject non-repositories, bare source workspaces, path traversal, and reparse escapes. For a normal repository require its Git directory under the workspace. For a linked worktree validate its registered worktree/admin relationship before accepting the common object directory; a caller-supplied `.git` pointer into an unrelated repository is not authority. Do not scan for neighboring repositories or accept a repository path in the tool payload.

Create disposable Git metadata under the Flowgency-owned scratch root, not in the source workspace. Initialize it with an empty template and the source object format. Use read-only access to the validated source object database for explicit objects; source refs/configuration are not copied wholesale. Reject unapproved object alternates, replacement refs, grafts, and reparse metadata instead of following them into other roots. Disable lazy fetching in partial clones. Any source metadata needed for identity or ref resolution is read with includes disabled and validated against the registered repository.

`source_git_dir` is the validated source administration directory; `object_view` is the disposable Git directory used by ordinary capture commands. Resolve Git, SSH, and any approved credential helper to absolute executables through a sanitized deployment PATH before using a repository cwd. Exclude relative PATH entries and binaries under the workspace/scratch roots so a repository-local `git.exe` cannot become the trusted tool on Windows.

Set a named process environment: required OS/PATH/temp variables only, a private HOME/config directory, `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_NO_LAZY_FETCH=1`, and `GIT_OPTIONAL_LOCKS=0`. Drop inherited `GIT_*`, credential, tracing, pager, SSH-command, and dynamic Git config injection variables before setting approved ones. Use `--no-pager`, `core.fsmonitor=false`, an empty `core.hooksPath`, and structured argument arrays. Never load ambient `url.*.insteadOf`, credential helper, diff helper, or text-conversion behavior into the private view.

`run_git_bytes` uses the extended supervisor, no shell, the remaining deadline capped at 30 seconds, and a bounded combined output budget. Translate timeout, nonzero exit, unavailable objects, and output overflow to fixed `GitEvidenceError` codes. Do not include raw command stderr or secret environment values in public errors. Cleanup the private metadata in `finally`, including failures and cancellation.

- [ ] **Step 4: Implement the committed-range algorithm and path permission gate.**

Use Git's structured NUL-delimited outputs for paths. Do not parse human-formatted status text or split paths on spaces/arrows. The command family is:

```python
commit_list_args = ("rev-list", "--reverse", "--topo-order", "--max-count=513", f"{base_commit}..{end_commit}", "--")
raw_args = ("diff", "--raw", "--no-abbrev", "-z", "--find-renames=50%", "-l1024", base_commit, end_commit, "--")
count_args = ("diff", "--numstat", "-z", "--find-renames=50%", "-l1024", base_commit, end_commit, "--")
patch_args = ("diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames=50%", "-l1024", base_commit, end_commit, "--")
```

Before these calls, require full object IDs of the repository's format, confirm both are commits, and require ancestor-or-equal using `merge-base --is-ancestor`. Reject revision expressions and leading-option tricks before invoking Git. Limit commit and file counts, parse rename records as two NUL-delimited paths, preserve old/new modes and object IDs, and treat `-` numstat values as binary rather than fabricated zero-line text changes.

Before capturing patch bytes, check every old/new path against both launch-time and current effective read policies. Deny absolute/parent-traversing paths and Git administrative paths. Check parent confinement/reparse safety without dereferencing a versioned symlink blob as a source file; Git reads that blob as committed text. For each policy, `tools_for(path) is None` or contains `read` is required. Fail the entire capture on one denied path. Do not recurse into submodules; record their pointer changes.

Retain the exact Git patch bytes; do not normalize newlines, decode/re-encode, or generate the patch from the UI parser. Use canonical metadata ordering for deterministic digests. Base equals end yields an honest empty diff. Revalidate repository identity before accepting the result so switching the source repository during capture fails rather than binds to a different repository.

- [ ] **Step 5: Extend the regression matrix and run the whole capture/supervisor slice.**

Add parameterized cases for additions/deletions/renames, binary content, symlink blobs, submodule pointers, file names with spaces/tabs/Unicode, SHA-1 and available SHA-256 repositories, empty ranges, missing commits, non-ancestor ranges, shallow/incomplete ancestry, command timeout, output caps, denied old rename paths, denied new paths, and a path under a more-specific no-read rule. Invalid encoded paths return a named unsupported-path error rather than dropping entries.

```python
def test_capture_requires_read_permission_for_every_selected_path(tmp_path):
    import time
    from flowgency.git_evidence.capture import capture_committed_range
    from flowgency.git_evidence.git import open_git_repository
    from flowgency.git_evidence.models import GitCommitRange, GitEvidenceError
    from flowgency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule
    from flowgency.jobs.processes import RuntimeProcessLifecycle
    from tests._git_evidence_helpers import create_git_repository

    fixture = create_git_repository(tmp_path / "repo")
    policy = EffectiveRuntimePolicy(timeout=30, mode="restricted", rules=(
        ResolvedPermissionRule(path=fixture.root, tools=("read",)),
        ResolvedPermissionRule(path=fixture.root / "result.txt", tools=()),
    ))
    lifecycle = RuntimeProcessLifecycle(job_id="capture-test", generation="generation-b")
    with open_git_repository(fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle) as repository:
        with pytest.raises(GitEvidenceError) as failure:
            capture_committed_range(
                repository, GitCommitRange(fixture.base_commit, fixture.end_commit),
                policies=(policy,), lifecycle=lifecycle, deadline=time.monotonic() + 120,
            )
    assert failure.value.code == "git-evidence-path-denied"
```

Use sentinel scripts to prove hostile diff helpers, hooks, fsmonitor, SSH commands, replacement refs, and config includes do not run or redirect reads. Snapshot source refs/index/worktree before and after every capture and assert byte/status parity.

Run: `python -m pytest tests/test_git_evidence.py tests/test_runtime_process_lifecycle.py -q`.

- [ ] **Step 6: Commit and review the subprocess/read boundary before local-ref verification.**

```powershell
git add flowgency/git_evidence/models.py flowgency/git_evidence/git.py flowgency/git_evidence/capture.py flowgency/jobs/processes.py tests/_git_evidence_helpers.py tests/test_git_evidence.py tests/test_runtime_process_lifecycle.py
git commit -m "feat(evidence): capture bounded committed git diffs"
```

Review subprocess containment, exact bytes, denied paths, linked worktrees, no ambient config authority, no silent empty success, and unchanged ordinary runtime supervision. This is a security-sensitive review gate.

### Task 3: Restrict Verification to Local Commits and Refs

**Files:**
- Modify: `flowgency/git_evidence/publication.py` (already created under the superseded remote contract).
- Modify: `flowgency/git_evidence/models.py`, `flowgency/git_evidence/git.py`.
- Modify: `flowgency/configuration/models.py`, `flowgency/configuration/paths.py` only where Task 1 added remote-only plumbing.
- Test: `tests/test_git_evidence.py`, `tests/_git_evidence_helpers.py`, `tests/test_config_patches.py`, `tests/test_workflow_configuration.py`, and existing config tests owning the removed remote path validators.

**Interfaces:**
- Consumes: Task 1's revised local-only `GitPublicationPolicy`, Task 2's `GitRepository`, `GitRangeCapture`, and supervised runner. No credential-eligibility argument remains.
- Produces: frozen `GitRefObservation(ref_object_id: str, commit_id: str)` in `models.py`, preserving annotated-tag identity separately from its peeled commit.
- Produces: frozen `GitPublicationReceipt(mode: Literal["local"], policy_digest: str, publication_ref: str | None, ref_object_id: str | None, observed_commit: str, verified_at: datetime)`.
- Produces: `verify_publication(repository: GitRepository, captured: GitRangeCapture, policy: GitPublicationPolicy | None, *, publication_ref: str | None, lifecycle: RuntimeProcessLifecycle, deadline: float, now: datetime) -> GitPublicationReceipt`.
- Produces: `resolve_publication_ref(repository: GitRepository, ref_name: str, *, lifecycle: RuntimeProcessLifecycle, deadline: float) -> GitRefObservation`, using a validated source ref and returning its full ref object and peeled commit IDs.

- [ ] **Step 1: Regress rejection of the superseded remote contract.**

Add the valid-under-old-code remote example from Task 1 as a rejection test, plus `mode: local` with each unsupported `remote`, `auth`, `known_hosts`, and agent-endpoint field. Exercise a real revision-checked config patch and assert the file is unchanged after invalid policy. Run `python -m pytest tests/test_git_evidence.py tests/test_config_patches.py -k policy -q` before removing support; record the real failures without converting the old remote-success tests into false RED evidence.

Keep the `git_publication` entry and local policy digest/context fence. Remove `GitRemotePolicy`, remote-only config parsing/path validators, obsolete error codes, adapter functions used only for remote config/transport, and the remote verifier/authentication constructors. Preserve shared ref/object validation, repository isolation, trusted Git executable selection, byte budgets, and ordinary config/path behavior. Do not retain ignored credential arguments, `remote_identity=None` compatibility fields, or dormant transport branches in this unshipped feature. Rerun the new rejection tests immediately after the first implementation edit.

- [ ] **Step 2: Complete and exercise the local-only verifier.**

```python
def test_local_publication_accepts_unpushed_commits(tmp_path):
    import time
    from datetime import datetime, timezone
    from flowgency.git_evidence import publication
    from flowgency.git_evidence.capture import capture_committed_range
    from flowgency.git_evidence.git import open_git_repository
    from flowgency.git_evidence.models import GitCommitRange, GitPublicationPolicy
    from flowgency.integrations.models import EffectiveRuntimePolicy
    from flowgency.jobs.processes import RuntimeProcessLifecycle
    from tests._git_evidence_helpers import create_git_repository

    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = RuntimeProcessLifecycle(job_id="capture-test", generation="local-only")
    with open_git_repository(fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle) as repository:
        deadline = time.monotonic() + 120
        captured = capture_committed_range(
            repository, GitCommitRange(fixture.base_commit, fixture.end_commit),
            policies=(EffectiveRuntimePolicy(timeout=30),), lifecycle=lifecycle, deadline=deadline,
        )
        receipt = publication.verify_publication(
            repository, captured, GitPublicationPolicy(mode="local"),
            publication_ref=None,
            lifecycle=lifecycle, deadline=deadline, now=datetime.now(timezone.utc),
        )
    assert receipt.mode == "local"
    assert receipt.observed_commit == fixture.end_commit
```

Run `python -m pytest tests/test_git_evidence.py -k local_publication -q`. If restricted refs exist, require a matching supplied ref, resolve/peel it safely, and prove the end is reachable. Without ref restrictions the verified commit range is sufficient and `ref_object_id` is `None`. With an explicit allowed ref, record its object and peeled commit IDs. An incomplete graph yields `git-evidence-verification-incomplete`; a complete graph disproving ancestry yields `git-evidence-not-published`, with a fixed message describing a local-ref mismatch rather than an unverified push. Missing policy remains an actionable configuration error.

- [ ] **Step 3: Prove transport independence and immutable local observations.**

Use real temporary repositories for allowed/disallowed heads, trailing-prefix allowlists, lightweight/annotated tags, packed refs, missing refs/objects, ancestor versus unrelated end commits, and absent policy. Preserve source bytes/refs/status before and after verification. Cover unsupported symbolic/reparse refs with explicit errors rather than reading outside the authorized repository.

On an otherwise successful fixture, change remote URLs, stale tracking refs, source credential-helper and `core.sshCommand` settings, and ambient SSH/credential variables. Local verification must still succeed without invoking those programs or a transport subcommand. Use controlled sentinel helpers/runner observation with a positive control, never user credentials, an actual network server, or the live origin. Compare the same receipt with a fixed `now` value. A receipt remains unchanged when the local ref moves later; a future capture observes its new local state. Do not replace behavioral checks with greps for absent implementation names.

Remove superseded remote-success/transport-quoting tests and remote-only fixture helpers. Retain local/capture/path safety coverage. Any test that already passes is regression evidence, not a manufactured pre-change failure.

- [ ] **Step 4: Run the affected slice and commit.**

Run `python -m pytest tests/test_git_evidence.py tests/test_config_patches.py tests/test_workflow_configuration.py tests/test_config.py tests/test_config_normalization.py tests/test_config_store.py -q`, plus the existing file that owns the removed raw path-validation tests. Run the local Git evidence slice in the retained WSL venv as a portability check; no dependency/bootstrap repetition is needed. No full-suite run belongs to this task.

```powershell
git add flowgency/git_evidence flowgency/configuration tests/test_git_evidence.py tests/_git_evidence_helpers.py tests/test_config_patches.py tests/test_workflow_configuration.py
git commit -m "refactor(evidence): keep verification local"
```

Stage only files changed for this task, including any affected existing config tests. Review removal of all unshipped remote policy/transport plumbing, unchanged unrelated credential boundaries, local receipt semantics, ref confinement, no source mutations, and regression coverage. The earlier remote quoting/agent-source findings are resolved by removing those paths, not by adding endpoint configuration or repairing authentication.

### Task 4: Bind Captures to Trusted Ticket Receipts and Enforce Them

**Files:**
- Create: `flowgency/tickets/git_evidence.py`.
- Modify: `flowgency/git_evidence/models.py`, `flowgency/tickets/service.py`, `flowgency/tickets/access.py`.
- Modify: `flowgency/tickets/errors.py` (one typed semantic-evidence error).
- Modify: `flowgency/web/dependencies.py`, `flowgency/jobs/execution.py` (live-service constructors).
- Modify: `tests/_git_evidence_helpers.py`.
- Test: `tests/test_ticket_artifacts.py`, `tests/test_ticket_transitions.py`, `tests/test_ticket_access.py`, `tests/test_ticket_service.py`.

**Interfaces:**
- Consumes: `TicketService._mutate`, `TicketStorage.apply/receipt/put_artifact/read_artifact`, Tasks 1-3's capture/policy APIs, `JobRecord.spec.runtime_policy.to_effective_policy()`, and `resolve_effective_policy`.
- Produces: `TicketAccessRegistry.resolve_context(context: AgentTicketContext) -> JobRecord`, returning the validated running record under the existing session/fingerprint/job lock. Existing `validate_context` delegates and discards the return value.
- Extends: `TicketService.__init__` with keyword-only `resolve_git_job: Callable[[AgentTicketContext], JobRecord] | None = None`. No resolver means capture unavailable, not a fallback to an untrusted path.
- Produces: `GitCaptureRequest(BaseModel)` with strict `transition_id`, `field_id`, `base_commit`, `end_commit`, and optional `publication_ref`; no actor, job, path, remote URL, policy, or patch bytes.
- Produces: `GitCaptureResult(BaseModel)` with `artifact: ArtifactRef`, `version: TicketVersion`, `mutation: TicketMutationResult`. A replay carries the original capture version; refresh before a new mutation if the ticket has since changed.
- Produces: `TicketService.capture_git_evidence(actor: AgentTicketContext, version: TicketVersion, request: GitCaptureRequest, operation: TicketOperation) -> GitCaptureResult`.
- Produces: `GitEvidenceManifest` and `GitCaptureReceipt` models in `tickets/git_evidence.py`, described below.
- Produces: `TicketEvidenceInvalid(TicketStorageError)` in `tickets/errors.py`, with `http_status = 422` for structurally invalid or untrusted evidence; preserve the existing exception vocabulary for other failures.
- Produces: `retained_git_artifact(captured: GitRangeCapture, receipt: GitPublicationReceipt, *, actor: AgentTicketContext, version: TicketVersion, request: GitCaptureRequest, policy: GitPublicationPolicy, captured_at: datetime) -> RetainedArtifact`.
- Produces: `validate_git_artifact(record: TicketRecord, artifact: RetainedArtifact, *, workspace: Path | None = None, policy: GitPublicationPolicy | None = None) -> GitEvidenceManifest`. It always verifies the trusted capture/manifest binding; supplied current workspace/policy additionally enforce transition-time compatibility. Read-only historical display omits current-policy checks and performs no Git/network access.

- [ ] **Step 1: Add a reusable service fixture using a real ticket provider and running job.**

Add `configure_git_ticket(env: WorkflowTestEnv, fixture: GitTestRepository, policy: GitPublicationPolicy) -> tuple[AgentTicketContext, TicketView]` to the Git test helper. Use the existing fixture methods, not a parallel fake storage implementation:

```python
def configure_git_ticket(env, fixture, policy):
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
        env.store.load().revision, env.blueprint_id, source.digest,
        WorkflowDefinition.model_validate(document),
    )
    authority = env.running_job("builder", "git-evidence-run")
    registry = TicketAccessRegistry(env.job_store)
    actor = registry.open(authority).context
    env.service.validate_agent_context = registry.validate_context
    env.service.resolve_git_job = registry.resolve_context
    ticket = env.create(values={"verdict": True, "summary": "Pending"})
    env.service.start_work(actor, ticket.version, env.operation("start", actor_name="builder"))
    return actor, env.read(ticket.ref)
```

The new `resolve_git_job` attribute is intentionally injectable on an individual fixture service; never monkeypatch a process-global submission function or install UI runtime state in the shared pytest process.

- [ ] **Step 2: Add the capture-without-transition regression.**

```python
def test_git_capture_records_receipt_without_advancing_ticket(workflow_env):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from flowgency.tickets.git_evidence import GitCaptureRequest
    from tests._git_evidence_helpers import configure_git_ticket, create_git_repository

    env = workflow_env
    fixture = create_git_repository(env.tmp_path / "source")
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    before = env.read(ticket.ref).record
    request = GitCaptureRequest(
        transition_id="complete", field_id="evidence",
        base_commit=fixture.base_commit, end_commit=fixture.end_commit,
    )
    result = env.service.capture_git_evidence(
        actor, ticket.version, request, env.operation("capture", actor_name="builder")
    )
    after = env.read(ticket.ref).record
    assert after.state_id == before.state_id
    assert after.field_values == before.field_values
    assert after.active_run == before.active_run
    assert after.revision == before.revision + 1
    assert after.events[-1].kind == "git-evidence-captured"
    assert after.events[-1].data["capture"]["artifact_id"] == result.artifact.value
    assert result.version.revision == after.revision
```

Run: `python -m pytest tests/test_ticket_artifacts.py -k git_capture -q`.
Expected before implementation: missing capture API, not a live runtime dependency.

- [ ] **Step 3: Define the immutable manifest and trusted receipt.**

Use strict, frozen Pydantic models with `extra="forbid"`. The manifest has `schema_version: Literal[1]`, `repository_id`, `workspace_identity`, `base_commit`, `end_commit`, `commit_ids`, `files`, `patch_b64`, `patch_sha256`, `team_id`, `workflow_id`, `ticket_id`, `binding_id`, `agent_name`, `job_id`, `captured_at`, `policy_digest`, a sanitized `policy_snapshot`, and `publication: GitPublicationReceipt`.

`workspace_identity` is the SHA-256 of the platform-normalized configured workspace path, not the exposed raw path. `policy_snapshot` contains only `mode: local` and allowed local refs; no remote identity, authentication profile, credential paths, or environment. `repository_id` is Task 2's stable authorized-repository identity. Enforce full-object/digest syntax, timezone-aware timestamps, counts, strict base64, decoded patch length, and exact `patch_sha256`. Check total canonical JSON length with `RetainedArtifact.create`; return an artifact-too-large error rather than truncating.

The event's `data["capture"]` contains a strict `GitCaptureReceipt`: `artifact_id`, `repository_id`, `workspace_identity`, `policy_digest`, `workflow_digest`, `context_digest`, intended `transition_id`, intended `field_id`, `agent_name`, and `job_id`. The outer event supplies its ID and timestamp. It intentionally duplicates only identity/check fields, not patch bytes, commit lists, or the full manifest. Generic upload/report/update commands cannot submit this event kind or shape.

Canonical encoding:

```python
patch_b64 = base64.b64encode(captured.patch).decode("ascii")
patch_sha256 = hashlib.sha256(captured.patch).hexdigest()
content = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
artifact = RetainedArtifact.create(
    "committed-changes.json", "application/vnd.flowgency.git-change+json", content
)
```

- [ ] **Step 4: Implement authority preflight and two-phase capture.**

Refactor `TicketAccessRegistry.validate_context` minimally into `resolve_context`, retaining all existing team/job/session, immutable-digest, running-state, and run-fingerprint checks under the same job lock. Add a return of the validated `JobRecord`; the old method continues to return `None`.

The capture API requires an authenticated agent and active ticket ownership. Preflight a current ticket/workflow/config snapshot, confirm the requested transition is available from the current state, and confirm its requested output is an artifact with `artifact_format="git-change"`. Reject missing policy. Resolve the actual running job via the injected registry, and require its `workspace_root` to match current team configuration. Check both its launch-time policy and current effective policy for reads. Capture uses no credentials and adds no workspace-write eligibility gate; preserve existing credential-withholding behavior elsewhere unchanged.

Before expensive work, consult the existing provider operation receipt after validating identity/session/team/binding. A matching replay returns the saved artifact and capture version without calling Git or contacting a remote. A reused operation ID with different request digest conflicts. Do not reject a genuine accepted replay solely because a later policy/definition changed; keep access and binding checks in force.

Release config/ticket locks during bounded local Git reads. Use a private scratch path under the job store's canonical artifact area, validate its confinement, and create a distinct capture generation. Call Tasks 2-3 without credential or remote arguments and construct the retained artifact. Then reacquire the existing workflow/config mutation guard, revalidate session, assignment, revision, definition digest, context digest, workspace and policy, and perform `provider.apply`. Inside its mutation callback, store the immutable artifact and append the trusted event. The artifact and ticket use separate existing locks: do not take them in reverse order elsewhere. A failed ticket write may leave an unreferenced immutable blob but must never leave an accepted output or success receipt.

Construct `GitCaptureResult` from the committed operation result and its capture event's stored digests. A version conflict after capture discards the proposed event; do not force a transition or retry with a guessed new version. Wire `registry.resolve_context` into both `web/dependencies.py` and `_ticket_runtime` in `jobs/execution.py`. Recovery-only constructors may leave capture disabled because they never issue capture operations.

Rerun the capture regression immediately, then `tests/test_ticket_access.py` before adding validation to consumers.

- [ ] **Step 5: Add evidence checks to every field-write and transition boundary.**

`validate_git_artifact` must find an exact trusted capture event in the same ticket, parse its receipt, read the artifact through the binding-scoped provider, verify the retained envelope and inner patch digest, and cross-check all receipt/manifest identity fields. Current transition/write checks also require the same workspace identity and current publication-policy digest. Do not require the current actor/job to equal the original producing actor/job: a reviewer may reuse verified evidence on the same ticket. Keep the original producer attribution and record the new transition actor separately.

Call this validator for non-null Git-format field values in:

- transition effective inputs and submitted outputs, before state/field mutation;
- explicit `ticket_update` field patches, before saving canonical fields;
- creation values, which cannot cite a receipt on a not-yet-existing ticket and therefore reject non-null Git evidence;
- other existing field-writing paths found by code-usage lookup, without changing ordinary artifact behavior.

Map failures consistently: missing/config-changed policy and stale versions use `TicketConflict` (409); denied paths or cross-ticket identity use `TicketForbidden` (403); missing stored artifacts use `TicketNotFound` (404); capture/artifact limits use `TicketTooLarge` (413); invalid ranges, forged/untrusted receipts, and semantic evidence mismatches use `TicketEvidenceInvalid` (422); unavailable Git/local verification graph uses `StorageUnavailable` (503). Corrupt persisted storage retains its existing corruption error. Pass only fixed public messages and codes, never raw Git stderr. All failures occur before the state/field mutation commits.

```python
class TicketEvidenceInvalid(TicketStorageError):
    http_status = 422
```

Use the snapshot already held by `_mutate`; do not reload policy after committing or perform network work in `evaluate_transition`. Propagate the snapshot/binding to `_update_record` and `_transition_record` narrowly rather than rereading global services. Historical read-only display verifies integrity and receipt binding but does not demand today's policy.

Add this success assertion after capture in the service test:

```python
accepted = env.service.transition(
    actor, result.version,
    env.transition_request(outputs={"summary": "Committed result", "evidence": result.artifact}),
    env.operation("complete-with-evidence", actor_name="builder"),
)
assert accepted.ticket.state_id == "done"
assert accepted.ticket.field_values["evidence"] == result.artifact
assert accepted.ticket.field_provenance["evidence"].job_id == actor.job_id
```

Then upload byte-identical-looking JSON through `publish_artifact` on a different ticket without a capture receipt, attempt to transition with it, and assert rejection with byte-identical ticket storage before/after. Repeat through `ticket_update` and with an HTTPS ArtifactRef. A trusted receipt with a changed policy, changed workspace, corrupt blob, wrong binding, or mismatched digest also fails before mutation.

- [ ] **Step 6: Prove replay and race behavior before committing.**

For a successful capture, replace the module's `capture_committed_range` callable with a function that raises if called, replay the same operation, and assert the same artifact, event, revision, and `replayed=True`. Mutate the policy afterward and repeat the accepted replay without a remote call; a new transition using the stale receipt must still fail. Test request-digest mismatch separately.

Pause a fake capture at a barrier, change the ticket revision/assignee or config policy/workspace/blueprint, then release it. Assert no capture event or field/state mutation is committed. Revoke the runtime session while paused and assert rejection. Test different tickets handled by the same job and same ticket evidence referenced by a later review job; never join by nearest timestamp.

Run: `python -m pytest tests/test_ticket_artifacts.py tests/test_ticket_transitions.py tests/test_ticket_access.py tests/test_ticket_service.py tests/test_git_evidence.py -q`.

```powershell
git add flowgency/git_evidence/models.py flowgency/tickets/git_evidence.py flowgency/tickets/errors.py flowgency/tickets/service.py flowgency/tickets/access.py flowgency/web/dependencies.py flowgency/jobs/execution.py tests/_git_evidence_helpers.py tests/test_ticket_artifacts.py tests/test_ticket_transitions.py tests/test_ticket_access.py tests/test_ticket_service.py
git commit -m "feat(tickets): validate trusted git evidence receipts"
```

Review all field-write entry points, replay before expensive operations, capture-lock ordering, active-session rechecks, and no relabeling of job summaries as evidence.

### Task 5: Expose Generic Capture and Artifact Authoring

**Files:**
- Modify: `flowgency/tickets/protocol.py`, `flowgency/tickets/mcp_server.py`, `flowgency/jobs/tickets.py`.
- Modify: `flowgency/static/workflow-editor.js`, `flowgency/workflows/forms.py`, `flowgency/tickets/views.py`.
- Modify: `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`, `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/references/ticket-workflow-steps.md`.
- Modify: `.github/skills/flowgency-setup/SKILL.md`, `.github/skills/flowgency-setup/references/ticket-workflow-steps.md`.
- Modify: `kb/data-formats.md`, `kb/configuration.md`, `config.yaml.example`.
- Test: `tests/test_ticket_mcp.py`, `tests/test_ticket_http_transport.py`, `tests/test_ticket_broker.py`, `tests/test_workflow_forms.py`, `tests/test_workflow_setup.py`, `tests/test_setup_assets.py`, `tests/ui/workflow_library.spec.ts`.

**Interfaces:**
- Consumes: Task 4's `GitCaptureRequest`, `GitCaptureResult`, and service capture API; Task 1's artifact format and policy.
- Produces: `TicketGitCaptureCommand` containing `version`, `operation_id`, and all `GitCaptureRequest` fields, with `extra="forbid"`.
- Produces: internal operation `capture_git_evidence` and MCP tool `ticket_capture_git_evidence(version, operation_id, transition_id, field_id, base_commit, end_commit, publication_ref=None)` returning the existing `TicketToolResponse` envelope with serialized `GitCaptureResult`.
- Produces: `artifact_format` on `WorkflowFieldDefinitionView` and `TicketFieldValueView`, preserving it in current definitions and historical field snapshots.

- [ ] **Step 1: Add typed-command and live tool-discovery tests.**

```python
def test_git_capture_command_rejects_caller_supplied_authority(workflow_env):
    from flowgency.tickets.protocol import InvalidTicketRequest, parse_ticket_command

    ticket = workflow_env.create()
    payload = {
        "version": ticket.version.model_dump(mode="json"),
        "operation_id": "capture-a", "transition_id": "complete", "field_id": "evidence",
        "base_commit": "a" * 40, "end_commit": "b" * 40,
        "workspace_path": "C:/outside", "job_id": "different-job",
    }
    with pytest.raises(InvalidTicketRequest):
        parse_ticket_command("capture_git_evidence", payload)
```

Extend the existing MCP tool-list/schema test with the new tool, required field list, description, and `additionalProperties: false`. Send a real authenticated request through the existing broker fixture and assert it dispatches to capture using the authenticated actor, not payload identity. Keep unauthenticated/cross-team requests rejected before Git calls.

Run: `python -m pytest tests/test_ticket_mcp.py tests/test_ticket_broker.py -k git_capture -q`.
Expected before adapters: unknown command/tool. Add the command to `TicketCommand`, `parse_ticket_command`, `dispatch_ticket_command`, and `register_remaining_ticket_tools`; construct the existing canonical operation digest from the typed payload. Do not create another HTTP endpoint outside the authenticated broker or bypass its validation middleware. Rerun the same tests immediately.

- [ ] **Step 2: Add artifact-format controls within the existing editor.**

For newly created artifact fields expose `New artifact format`, with option values `""` (label `File`) and `"git-change"` (label `Git changes`). For an existing artifact field use `Input artifact format N` or `Output artifact format N`, following the existing field-row naming pattern. Only artifact fields expose this select. Store the value on the shared field object so every use reflects it. Switching a field to a non-artifact type clears the format in the local draft; the server still rejects invalid combinations from raw clients.

Carry `artifact_format` through field creation, draft serialization, saved-payload rebasing, and re-rendering. Do not introduce a second field ID, hidden naming convention, or `evidence_required` flag.

```typescript
test('git artifact format survives authoring and reload', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await openTransitions(page);
  await page.getByLabel('Add output', { exact: true }).click();
    await page.getByLabel('New field label').fill('Committed implementation');
  await page.getByLabel('New field type').selectOption('artifact');
  await page.getByLabel('New artifact format').selectOption('git-change');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();
  await expect(page.getByLabel('Output required 3', { exact: true })).toBeChecked();
  await saveEditor(page);
  await page.reload();
  const payload = await readEditorPayload(page);
    const field = payload.draft.fields.find((row: { label: string }) => row.label === 'Committed implementation');
  expect(field.type).toBe('artifact');
  expect(field.artifact_format).toBe('git-change');
  await openTransitions(page);
  await expect(page.getByLabel('Output artifact format 3', { exact: true })).toHaveValue('git-change');
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});
```

Run this test before the JavaScript edit to observe the absent control, then after it. Extend it to reuse the field as a later transition input, toggle required/optional without losing the format, and switch another field to text while clearing its format. Run all four configured projects.

- [ ] **Step 3: Teach setup and agents the generic Git-evidence sequence.**

Document the actual new tool signature, its returned artifact/version, explicit commit endpoint selection, and two separate actions: perform project-authorized commit work (and push separately if project instructions require it), then capture and submit local evidence. Explain that capture does not commit, push, fetch, verify a push, or make unrelated commits attributable to a ticket. A report, an uploaded patch, and a remote web URL are not substitutes for a Git-format result.

In setup's definition process, ask whether each produced artifact requires Git evidence. For code-producing projects, propose the corresponding output format and local commit/ref policy for approval. Remote push obligations remain instructions, not evidence configuration. Do not add a required Git field to every shipped Software delivery or Research transition. Preserve the user's existing live configured library/config; setup writes only through its normal approved flow.

Show these exact canonical examples in `kb/configuration.md` and a commented example in `config.yaml.example`:

```yaml
git_publication:
  mode: local
  allowed_refs: []
```

```yaml
git_publication:
    mode: local
  allowed_refs: [refs/heads/main, 'refs/heads/feature/*']
```

Show a generic artifact field, independent of workflow/agent names:

```yaml
fields:
  - id: implementation
    label: Committed implementation
    type: artifact
    artifact_format: git-change
```

Document the local-only schema, optional ref restrictions, missing-local-graph error, and local snapshot-at-verification-time semantics. There are no remote/authentication options and no verified-push status. Runtime never parses the constitution directly. Update the package-owned skill source reached by the discovery symlink and retain wheel/parity tests.

Execution preflight ruling, approved by the user on 2026-09-18: do not add exact-text guidance assertions. Use policy, service, and tool tests to prove local support, approved local refs, rejection of remote/authentication configuration, and failure without policy. Use isolated consumer exercises under the writing-skills workflow to verify that a local-only project does not trigger a push, a project requiring push in its instructions does not invent a remote verification tool/config field, and absent policy leads to a configuration blocker rather than an invented grant. Provide typed mocked tool responses only; no live project tools or user agents. Retain prompts, observed tool payloads, and outcomes in the task report. Packaging parity proves delivery of those instructions, not their behavioral effect.

- [ ] **Step 4: Run adapters, authoring, and packaged-guide gates; commit and review.**

```powershell
python -m pytest tests/test_ticket_mcp.py tests/test_ticket_http_transport.py tests/test_ticket_broker.py tests/test_workflow_forms.py tests/test_workflow_setup.py tests/test_setup_assets.py -q
node ./node_modules/@playwright/test/cli.js test tests/ui/workflow_library.spec.ts
```

Stage only the files in this task. Commit implementation/adapters with `feat(workflows): expose git evidence authoring`; commit user-facing documentation separately with `docs(workflows): explain committed git evidence`. Review the tool allowlist/schema, no caller-controlled authority, preserved required flags, and parity between documented and implemented policy fields.

### Task 6: Render Immutable Diffs and Link Their Exact Provenance

**Files:**
- Create: `flowgency/web/git_evidence.py`, `flowgency/web/routes/git_evidence.py`.
- Create: `flowgency/templates/git_evidence.html`, `flowgency/static/git-evidence.css`.
- Modify: `flowgency/app.py` (router registration only).
- Modify: `flowgency/tickets/views.py`, `flowgency/templates/_ticket_inspector.html`.
- Modify: `flowgency/web/routes/jobs.py`, `flowgency/templates/job_detail.html`.
- Modify: `pyproject.toml` (add `unidiff>=0.7.5,<0.8`).
- Test: `tests/test_ticket_routes.py`, `tests/test_job_routes.py`, `tests/test_ticket_artifacts.py`, `tests/test_git_evidence.py`.

**Interfaces:**
- Consumes: Task 4's trusted artifact/receipt validation without current-policy enforcement on reads, Task 2's file metadata, existing team/workflow context and artifact provider.
- Produces: `GitEvidenceSummaryView` in `tickets/views.py` with `artifact_id`, `repository_id`, `base_commit`, `end_commit`, `job_id`, `agent_name`, `captured_at`, `publication_mode`, `publication_ref`, `observed_commit`, `verified_at`, `file_count`, `lines_added`, and `lines_removed`.
- Extends: `TicketFieldValueView.git_evidence: GitEvidenceSummaryView | None = None`, `.evidence_issue: ViewIssue | None = None`, and `TicketAuditEventView.git_outputs: dict[str, GitEvidenceSummaryView]` plus `.evidence_issues: tuple[ViewIssue, ...]`. Preserve original `event.data` exactly.
- Produces: frozen `GitDiffLine(kind: Literal["context", "added", "removed", "marker"], old_line: int | None, new_line: int | None, text: str)`, `GitDiffFile(anchor: str, change: GitFileChange, lines: tuple[GitDiffLine, ...])`, and `GitDiffPreview(files: tuple[GitDiffFile, ...], omitted: bool, unavailable_reason: str | None)` in `web/git_evidence.py`.
- Produces: `parse_git_diff(manifest: GitEvidenceManifest, *, max_bytes: int = 204800, max_lines: int = 2000) -> GitDiffPreview` and `load_ticket_git_evidence(service: TicketService, actor: UserTicketContext, ref: TicketRef, artifact_id: str) -> tuple[RetainedArtifact, GitEvidenceManifest]`.
- Produces: `job_git_evidence_links(service: TicketService, actor: UserTicketContext, record: JobRecord) -> tuple[tuple[dict[str, str], ...], tuple[ViewIssue, ...]]`, where each link has `label`, `href`, and `ticket_href` and requires an accepted output plus exact trusted producer job identity.
- Routes: `GET /{team}/workflows/{workflow}/tickets/{ticket}/artifacts/{artifact_id}/diff` and the sibling `/patch` route. No POST operation is added to the viewer.

- [ ] **Step 1: Add a retained-byte viewer/download test using a real capture.**

Reuse `configure_git_ticket` and the real capture/transition sequence from Task 4 in `tests/test_ticket_routes.py`. Record the returned artifact ID, alter the source workspace after the transition, and prove both endpoints still show the retained result:

```python
def test_git_diff_download_is_retained_not_recomputed(workflow_web_env):
        import base64
        import json
        from flowgency.git_evidence.models import GitPublicationPolicy
        from flowgency.tickets.git_evidence import GitCaptureRequest
        from tests._git_evidence_helpers import configure_git_ticket, create_git_repository

        env = workflow_web_env
        fixture = create_git_repository(env.tmp_path / "source")
        actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
        captured = env.service.capture_git_evidence(
                actor, ticket.version,
                GitCaptureRequest(transition_id="complete", field_id="evidence", base_commit=fixture.base_commit, end_commit=fixture.end_commit),
                env.operation("capture", actor_name="builder"),
        )
        env.service.transition(
                actor, captured.version,
                env.transition_request(outputs={"summary": "Committed result", "evidence": captured.artifact}),
                env.operation("finish", actor_name="builder"),
        )
        retained = env.current_provider().read_artifact(ticket.ref, captured.artifact.value)
        expected_patch = base64.b64decode(json.loads(retained.content)["patch_b64"], validate=True)
        (fixture.root / "result.txt").write_bytes(b"unrelated later edit\n")
        base_url = f"{env.base_path}/tickets/{ticket.ref.ticket_id}/artifacts/{captured.artifact.value}"
        viewer = env.client.get(f"{base_url}/diff")
        download = env.client.get(f"{base_url}/patch")
        assert viewer.status_code == 200
        assert "after" in viewer.text
        assert "unrelated later edit" not in viewer.text
        assert download.status_code == 200
        assert download.content == expected_patch
        assert download.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in download.headers["content-disposition"]
```

Run: `python -m pytest tests/test_ticket_routes.py -k git_diff_download -q`.
Expected before routes: 404. The source modification must not affect expected bytes because they come from the retained artifact.

- [ ] **Step 2: Add the parser dependency and a bounded presentation model.**

Use `unidiff.PatchSet` on the complete retained patch only after artifact size/integrity validation. Parse at most the existing 640 KiB capture cap; then limit displayed complete hunks/rows to the preview byte/line budgets. Do not slice raw patch bytes mid-hunk and hand an invalid fragment to the parser. Track omitted content separately. Use trusted manifest file metadata for binary, mode-only, and submodule changes and for the full file list/counts.

```python
from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

patch_text = patch_bytes.decode("utf-8")
parsed = PatchSet(patch_text.splitlines(keepends=True))
for patched_file in parsed:
        for hunk in patched_file:
                for line in hunk:
                        kind = {"+": "added", "-": "removed", " ": "context"}.get(line.line_type, "marker")
                        row = GitDiffLine(kind, line.source_line_no, line.target_line_no, line.value)
```

This loop feeds the bounded builder, not HTML. Preserve no-newline markers. For non-UTF-8 or parser-unsupported content return a specific preview-unavailable reason and keep the exact download available; do not alter captured bytes or pretend an empty diff. An empty range has a distinct no-net-changes state. Treat manifest corruption as an integrity error, not a parser fallback.

- [ ] **Step 3: Implement confined read-only routes and safe navigation.**

Resolve team/workflow/ticket through existing service context, construct a bound `TicketRef`, and read the exact artifact through the existing storage provider. Require its same-ticket trusted capture receipt; a hash in a sibling ticket/workflow or a media type alone is insufficient. The route accepts IDs only, never a path. Return 404 for missing artifacts/tickets, 403 for denied access, the existing corruption status for damaged storage, 422 for an unverified/invalid evidence manifest, and 413 for an oversized artifact. Show a safe human-readable error without storage paths, stderr, or credentials.

Perform filesystem/parse work in `run_in_threadpool`. The patch route returns the decoded retained bytes with fixed attachment disposition, `application/octet-stream`, `nosniff`, and a safe filename. It must not call Git. The viewer supports an allowlisted `source=ticket|job` query only. For `source=job`, derive the job ID from the trusted manifest and verify it belongs to the same team; never accept a return URL. If that job was removed, return navigation falls back to the ticket without hiding the retained evidence.

Register the new router through the app's existing registration pattern. Do not redesign `app.py` or the existing job artifact route.

- [ ] **Step 4: Build the unified-diff page using escaped semantic HTML.**

Use the existing application shell and Lucide icons. The page is an unframed work surface, with compact repository/revision/local-ref metadata, file navigation, download action, and one diff section per file. Label the evidence `Local commits` and never show a remote-publication status. Use indexed anchors such as `change-0`; do not place raw paths in DOM IDs.

```jinja2
<nav aria-label="Changed files">
    {% for file in preview.files %}
    <a href="#{{ file.anchor }}">{{ file.change.path }}</a>
    {% endfor %}
</nav>
{% for file in preview.files %}
<section id="{{ file.anchor }}" aria-label="{{ file.change.path }}">
    <h2>{{ file.change.path }}</h2>
    <div class="git-diff-scroll" tabindex="0" aria-label="Diff for {{ file.change.path }}">
        <table class="git-diff-table">
            <thead><tr><th>Old</th><th>New</th><th>Change</th><th>Code</th></tr></thead>
            <tbody>
                {% for line in file.lines %}
                <tr class="git-diff-{{ line.kind }}">
                    <td>{{ line.old_line if line.old_line is not none else '' }}</td>
                    <td>{{ line.new_line if line.new_line is not none else '' }}</td>
                    <td>{{ '+' if line.kind == 'added' else '-' if line.kind == 'removed' else '' }}</td>
                    <td><code>{{ line.text }}</code></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</section>
{% endfor %}
```

Use Jinja autoescaping; no `safe`, raw HTML from the parser, or syntax highlighter that inserts unsanitized markup. Style code with whitespace preservation and overflow inside `.git-diff-scroll`, not the entire page. File labels wrap. Keep numeric columns stable and heading text compact. Add an accessible skip link, visible focus, and textual labels for binary/submodule/mode-only changes. Display preview omission and no-net-changes as separate states. Use existing light/dark tokens, not a new theme.

- [ ] **Step 5: Add verified links to current fields, History, and producing Job Detail.**

In the shared ticket detail projection, verify each distinct candidate artifact at most once per request and cache by artifact ID within that ticket. Derive summaries from retained manifests and receipts without Git/network work or requiring today's policy. Attach safe `evidence_issue` values to failures so one bad artifact does not erase other ticket fields. Keep audit `data` unmodified; populate `git_outputs` separately for accepted transitions' artifact output values. A prior ordinary artifact can still be shown with its ordinary download behavior when no trusted Git receipt exists; it must not receive a verified label.

Extend `render_value(field_type, value, git_evidence=None)` so a verified evidence value shows `View diff` and `Download patch`; ordinary artifacts retain their existing links. Use the current field summary for Overview and each event's summary map for History. After a format declaration is removed, a valid captured historical output can still link from its trusted receipt. Do not synthesize current field values from that history.

`job_git_evidence_links` lists the team's currently configured workflows through `TicketService`, finds accepted transition output references, validates their captures, and includes only manifests whose `job_id` and producer agent exactly match the requested persisted job. Deduplicate by ticket/artifact. A later reviewer referencing another job's artifact does not become the producer. Return scoped warnings for unavailable namespaces; do not glob runtime directories or use timestamp/name guesses. Add the links to `_job_detail_context` in `web/routes/jobs.py` via the route's injected service, with a small `Git evidence` section in the existing template. Leave `Changed files` unchanged and separate.

- [ ] **Step 6: Add confinement, presentation, and provenance regression cases.**

Extend the retained-download test by moving/deleting source refs, temporarily making the source repository unavailable, and changing current policy; already-retained evidence must remain readable. Corrupt the stored envelope/manifest/patch digest separately and expect explicit errors without regeneration.

Parameterize wrong team/workflow/ticket, traversal-like artifact IDs, a symlink/reparse artifact envelope, forged generic upload, arbitrary `source` query, unknown job, and a captured-but-unsubmitted artifact. The last may be viewed directly through its receipt but must not appear as a completed ticket output or accepted Job Detail link. Test two job IDs and a later review event to prove exact producer association.

Use an actual committed file containing `<script>` and event-handler text to prove escaped rendering; binary bytes and no-newline content must download byte-exactly. Assert the preview budget does not truncate the download. Run:

```powershell
python -m pytest tests/test_ticket_routes.py tests/test_job_routes.py tests/test_ticket_artifacts.py tests/test_git_evidence.py -q
```

- [ ] **Step 7: Commit and review the read path independently.**

```powershell
git add flowgency/web/git_evidence.py flowgency/web/routes/git_evidence.py flowgency/templates/git_evidence.html flowgency/static/git-evidence.css flowgency/app.py flowgency/tickets/views.py flowgency/templates/_ticket_inspector.html flowgency/web/routes/jobs.py flowgency/templates/job_detail.html pyproject.toml tests/test_ticket_routes.py tests/test_job_routes.py tests/test_ticket_artifacts.py tests/test_git_evidence.py
git commit -m "feat(evidence): view retained committed git diffs"
```

Review that every displayed verified link has a receipt, reads never invoke Git/network, the complete patch stays downloadable, and no new raw HTML or arbitrary redirect/path boundary was introduced.

### Task 7: Verify the End-to-End Browser Flow and Package Boundaries

**Files:**
- Modify: `tests/ui/server.py` (one opt-in `git-evidence` fixture only).
- Create: `tests/ui/git_evidence.spec.ts`.
- Modify: `tests/test_setup_assets.py` or the existing packaging test that owns static/template inclusion.
- Test: existing repository boundary, ticket runtime protocol, and full browser suites.

**Interfaces:**
- Consumes: Tasks 4-6's public service/tool/viewer contracts and the existing `POST /__ui/reset` test-only selection mechanism with JSON body `{"fixture": "git-evidence"}`.
- Produces: `GIT_EVIDENCE_FIXTURE = "git-evidence"`, `_seed_git_evidence_fixture(runtime: Path, config: dict) -> None`, and one deterministic isolated fixture ticket `fixture-git-evidence` in `newsletter/delivery`.
- Produces: browser proofs across desktop-light, desktop-dark, mobile-light, and mobile-dark using the existing one-worker Playwright configuration.

- [ ] **Step 1: Add an opt-in fixture using actual Git capture and the real ticket provider.**

Add the fixture name to `SUPPORTED_UI_FIXTURES` before mutation. Seed its repository only under the test runtime workspace, with deterministic author/committer identity and dates. Set that fixture's policy to local-only and author a Git-format output on its test workflow. Use the existing test job helpers to create a running, fixture-owned job, open its `TicketAccessRegistry`, start work, capture via the real service, and accept the transition. Then settle/close that fixture job without launching an AI runtime. Retain both an earlier and later evidence output on the ticket for History coverage, with distinct capture events and commit ranges.

Do not create a new production fixture endpoint. Do not import/install the UI runtime inside the shared Python test process; any packaging/reset test exercising its global installer must use an isolated child interpreter. Preserve workflow-library and job-store roots during reset and clear seeded contents rather than replacing authority directories while requests may be reading them. Restore the default fixture after each browser test.

The fixture artifact must include text additions/deletions, a renamed long-path file, a small binary file, and malicious-looking source text that remains inert. Use separate captures for empty/large preview cases rather than weakening limits. Record no live user paths or credentials in screenshots.

- [ ] **Step 2: Add the ticket-to-diff-to-download browser regression.**

```typescript
import { expect, test } from '@playwright/test';
import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';

test.beforeEach(async ({ page, request }, testInfo) => {
    const reset = await request.post('/__ui/reset', { data: { fixture: 'git-evidence' } });
    expect(reset.status()).toBe(204);
    await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
    await page.close();
    expect((await request.post('/__ui/reset')).status()).toBe(204);
});

test('closed ticket opens its retained diff and returns', async ({ page, request }) => {
    const ticketUrl = '/newsletter/workflows/delivery/tickets/fixture-git-evidence';
    await page.goto(ticketUrl);
    const overview = page.locator('[data-ticket-panel="overview"]');
    await overview.getByRole('link', { name: 'View diff', exact: true }).click();
    await expect(page.getByRole('navigation', { name: 'Changed files', exact: true })).toBeVisible();
    await expect(page.getByText('Local commits', { exact: true })).toBeVisible();
    const downloadUrl = await page.getByRole('link', { name: 'Download patch', exact: true }).getAttribute('href');
    expect(downloadUrl).toBeTruthy();
    const patch = await request.get(downloadUrl!);
    expect(patch.ok()).toBeTruthy();
    expect(patch.headers()['x-content-type-options']).toBe('nosniff');
    expect((await patch.body()).toString('utf8')).toContain('diff --git');
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
    await page.getByRole('link', { name: 'Back to ticket', exact: true }).click();
    await expect(page).toHaveURL(ticketUrl);
});
```

Run: `node ./node_modules/@playwright/test/cli.js test tests/ui/git_evidence.spec.ts`.
Assert the fixture's exact retained download digest in addition to the illustrative `diff --git` content assertion. Scope locators to the current output because History contains separate earlier links.

- [ ] **Step 3: Cover History, producing jobs, responsive layout, and failure states.**

Open an earlier History result and verify its base/end IDs and patch differ from the latest value. Open its producing Job Detail and follow only the matching artifact link, then verify `Back to job`. A job with only `changed_files` has no verified link. Confirm a later reviewer does not inherit producer links.

Use existing keyboard helpers to reach file navigation, download, and return links. Test 320px width, horizontal scrolling within the code pane only, long filenames, light/dark contrast, and a no-JavaScript context. Assert malicious source text did not execute, binary changes are labeled without fake hunks, no-net-changes is distinct from unavailable preview, and truncated preview still downloads the complete artifact.

Capture desktop/mobile screenshots in all four projects and inspect actual PNGs. Compare layout to the specification's text and the existing application shell; no approved mockup paths exist. Do not update unrelated snapshots or increase global tolerances. A browser fault or unavailable CDN is not permission to accept an unstyled baseline.

- [ ] **Step 4: Verify package contents and runtime tool availability without user-agent runs.**

Extend the existing wheel inspection to assert the new template/static files and all new Python modules are included. Verify that installed package imports resolve from the wheel extraction, not the source checkout. The authenticated HTTP/MCP tests must list and execute the capture tool against an isolated Git fixture. The fake runtime fixture must not leak global registry/submission changes into later pytest tests.

Run:

```powershell
python -m pytest tests/test_setup_assets.py tests/test_repository_boundaries.py tests/test_ticket_mcp.py tests/test_ticket_http_transport.py tests/test_ticket_artifacts.py tests/test_git_evidence.py -q
node ./node_modules/@playwright/test/cli.js test tests/ui/git_evidence.spec.ts tests/ui/workflow_library.spec.ts tests/ui/workflow_board.spec.ts
```

- [ ] **Step 5: Commit coverage and finish the task review.**

```powershell
git add tests/ui/server.py tests/ui/git_evidence.spec.ts tests/test_setup_assets.py
git commit -m "test(evidence): verify retained diff workflows"
```

Include only this fixture's reviewed snapshots if the existing screenshot convention requires committed baselines. Review reset isolation, exact-byte download assertions, absence of real network/agent work, and all four viewport/theme results.

## Whole-Feature Verification and Integration

- [ ] Check both plans against the specification acceptance list and review the entire branch, including authority, filesystem/network safety, process cleanup, capture replay, and presentation. Do not dispatch implementation agents until the user selects an execution mode.
- [ ] Run `python -m pytest tests/ -q` and `node ./node_modules/@playwright/test/cli.js test` from the feature worktree, without snapshot-update mode. Record exact counts, genuine skips, warnings, and retained report paths; do not combine focused-pass counts with a failed full-suite result.
- [ ] Ensure Git subprocess timeout/output-limit cleanup passes on Windows and through the repository's POSIX test/CI coverage. Any unavailable platform gate must be reported, not silently claimed.
- [ ] Confirm only approved code/docs/tests/dependencies changed. Do not stage `config.yaml`, locks, live agent libraries, team state, logs, or unrelated main-worktree changes.
- [ ] Follow the foundation plan's integration sequence: preserve main changes, rebase only if needed to fast-forward, rerun suites, fast-forward `master`, verify both suites on main, push main and feature, archive reports, remove the worktree normally, and retain the branch.
- [ ] For user inspection after implementation, provide the isolated fixture's viewer URL or the verified live route for newly created evidence only. Do not launch a live agent, generate a historical patch, or edit a ticket merely to obtain a demo link.

## Specification Coverage

| Requirement | Tasks |
| --- | --- |
| Generic fields, required/optional output roles, latest value and historical output retention | Foundation Tasks 1-3 |
| Optional Git artifact contract and project-defined local commit/ref policy | 1, 3, 5 |
| Exact committed ranges, no dirty-file inference, bounded Git execution | 2 |
| Offline local-ref checks; no remote verification or authentication configuration | 3 |
| Permission, path, job, session, ticket, and policy binding | 2, 3, 4 |
| Trusted artifacts, no generic-upload spoofing, atomicity, replay, and concurrent changes | 4, 5 |
| Immutable bytes after ref/repository changes, historical policy receipts | 4, 6 |
| Viewer/download/current/History/exact-job navigation | 6, 7 |
| Binary/submodule/empty/oversized/malicious content and accessible responsive UI | 2, 6, 7 |
| Setup sources, explicit local-ref policy, project push instructions kept separate, packaged examples/docs | 5, 7 |
| No backfills, live-data edits, automatic commits/pushes, or permission widening | Every task and whole-feature review |