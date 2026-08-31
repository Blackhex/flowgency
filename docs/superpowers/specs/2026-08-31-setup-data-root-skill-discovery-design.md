# Setup Data Root And Skill Discovery

**Date:** 2026-08-31
**Status:** Approved (design)

## Problem

Guided first-run setup labels its only path input as `Project folder`, passes it
through the application as `project_dir`, and launches the selected integration
from that directory. The field is actually intended to select Agency's
permanent data root. This misleading contract also leaves Copilot unable to
discover `agency-setup` unless the selected directory happens to expose the
repository's `.github/skills/agency-setup` link. A normal installed first launch
therefore opens a session that reports:

```text
skill(agency-setup) Skill not found: agency-setup
```

The browser, launch request, prompt, integration, packaged resources, and setup
skill must agree about both paths: the browser selects the Agency data root,
while the setup conversation selects the first group's project workspace.

## Goals

1. Present the existing setup path field unambiguously as the Agency data root.
2. Accept an existing safe data root or safely create a missing absolute root.
3. Use the selected data root as the interactive setup session's working
   directory.
4. Make the canonical `agency-setup` skill discoverable from editable and wheel
   installations without requiring project-local or user-global installation.
5. Ask for the first group's project workspace in the setup conversation.
6. Preserve the final consolidated path approval, complete schema-version-5
   validation, and one revision-checked atomic configuration write.

## Non-Goals

- Adding an `agency.data_root` field to the configuration schema.
- Treating the Agency data root as a group execution workspace.
- Installing or updating a user-global Copilot skill.
- Writing Copilot-native skill files into the selected data root.
- Creating derived storage directories before conversational path approval.
- Changing dashboard polling, config authority, or runtime path ownership.
- Migrating data for an existing configured Agency installation.

## Superseded First-Run Contract

This design supersedes only the first-run handoff and root-question ordering in
`2026-07-23-agency-data-root-setup-design.md`. That design deliberately kept the
data root off the browser form and made it the skill's first question. The
browser now selects the data root before launch, so the project workspace
becomes the first setup-session question.

The prior path derivation, advanced override, consolidated summary, safety, and
atomic-write requirements remain in force. Creating the selected root itself
before the conversation is the one deliberate exception to the previous
creation timing; all directories derived beneath it still wait for final path
approval.

## User Experience

The setup page retains one path field and one integration selector. The path
surface uses the following terms consistently:

- Introductory copy: choose the Agency data root and launch-capable integration.
- Field label: `Agency data root`.
- Windows placeholder: `C:\Agency`.
- Directory dialog title: `Choose Agency data root`.
- Selection feedback: `Agency data root selected.`
- Validation errors: refer to an Agency data root, never a project folder.
- Waiting summary label: `Agency data root`.

The directory browser continues to select existing directories. Direct text
entry additionally permits a missing absolute path that Agency can safely
create. The browser does not gain a directory-creation workflow.

After launch, the guided conversation treats the supplied root as selected and
asks for the first group's project workspace before inspecting project source or
asking team questions. It later shows both the data root and project workspace
in the existing consolidated path summary.

## Architecture

### Setup Root Preparation

The web setup boundary renames `project_dir` to `data_root` in form values,
template context, route-local names, `InteractiveSetupRequest`, and integration
launch methods. This is an intentional internal contract change; no compatibility
alias preserves the misleading name.

A setup-root preparation helper applies the same creatable-directory semantics
already used by configuration path validation. The existing generic check is
promoted or reused rather than copied into the route. Preparation performs these
steps before constructing a launch request:

1. Expand user-home syntax and require an absolute path.
2. Resolve the path without requiring the final directory to exist.
3. Reject a file, symlink, Windows reparse point, unreadable or unwritable
   existing directory, or missing path without a writable real nearest parent.
4. Create only the selected root, including missing ordinary parents.
5. Resolve and revalidate the created directory strictly before launch.

This preflight may leave an empty root behind if a later terminal launch fails.
It never creates `agent-library`, `compiled-agents`, `memory`, `prompts`, or
`groups` and never writes configuration. Agency does not remove the root or any
missing parents it created: the user explicitly selected that permanent
location, and deleting it after a launch failure could race with user content.
A relaunch accepts and revalidates the now-existing root idempotently.

### Packaged Skill Ownership

The canonical `agency-setup` files live in package data beneath a Copilot
discovery root with this logical shape:

```text
agency/setup_assets/copilot/
`-- .github/
    `-- skills/
        `-- agency-setup/
            |-- SKILL.md
            `-- references/
```

`pyproject.toml` declares the skill through `[tool.setuptools.package-data]`.
The declaration includes `SKILL.md` and regular Markdown files directly beneath
`references/`; the packaging test recursively compares all regular source files
under `agency-setup` with the wheel entries so adding a reference without
packaging it fails immediately. This includes the current
`dispatch-templates.md`, `observation-system-steps.md`, and `templates.md`
references in editable installations, source distributions, and wheels.

The repository paths `skills/agency-setup` and
`.github/skills/agency-setup` resolve to this same canonical source so repository
discovery, documentation paths, package runtime behavior, and tests cannot
drift between copies.

The Copilot integration resolves the installed discovery root as
`Path(agency.__file__).resolve().parent / "setup_assets" / "copilot"`. A normal
editable or wheel installation exposes package resources as stable filesystem
paths for the lifetime of the launched terminal, so no temporary extraction
context is used. The integration verifies that
`.github/skills/agency-setup/SKILL.md` is a real readable file and owns the
Copilot-specific launch argument. Other integrations receive the data-root
working-directory contract but remain responsible for exposing the standard
skill through their own supported mechanism.

### Copilot Launch Contract

The interactive setup command includes both roots explicitly:

```text
copilot -C <data-root> --add-dir <packaged-discovery-root> \
  -i <setup-prompt> --name "Agency setup"
```

The process working directory and `-C` value are the strictly resolved data
root. The packaged discovery root is read-only application content and is never
configuration authority. The generated fallback command carries the same
arguments.

Before launch, Agency checks
`<data-root>/.github/skills/agency-setup`. It proceeds when that path is absent
or resolves to the packaged canonical skill, and rejects any other local entry.
This prevents a stale or unrelated local skill from shadowing the bundled setup
contract. Other integration-native content in the root remains untouched.

### Prompt And Skill Contract

`build_setup_prompt` emits a natural-language context block with these explicit
lines:

```text
Setup mode: guided-first-run.
Agency data root: <strict absolute path>.
Authoritative config: <strict absolute path>.
Selected integration: <registered integration name>.
```

These lines supply three distinct values:

- Agency data root: selected in the browser and already created.
- Authoritative config path: supplied by the running Agency process.
- Selected integration: used for initial defaults unless the user approves a
  different registered integration.

The prompt then states: `The Agency data root was selected in the browser; do
not ask for it again. Ask for the first group project workspace as the first
user-facing question.` It instructs `agency-setup` to inspect that workspace
read-only only after selection, derive default storage beneath the supplied data
root, and retain the existing grouped override and consolidated approval flow.

The canonical skill supports both invocation contexts without ambiguity:

- A prompt containing both `Setup mode: guided-first-run.` and an
   `Agency data root:` line does not ask for that root again; it asks for the
   project workspace first.
- Any manual invocation without that complete guided context retains a root
   question, followed by the project workspace question. No environment variable
   or hidden process state selects a mode.

In both cases the skill keeps Agency-owned storage disjoint from project source
and writes only one complete schema-version-5 configuration atomically.

## Data Flow

1. The user enters or browses to an Agency data root and chooses an integration.
2. The setup route validates and, when necessary, creates only the root.
3. The route verifies that no local `agency-setup` shadows the packaged skill.
4. It builds an `InteractiveSetupRequest` carrying the data root, config path,
   and setup prompt.
5. Copilot validates its packaged discovery tree and builds a command using the
   data root for both process cwd and `-C`, plus the package root for
   `--add-dir`.
6. The setup session loads `agency-setup` and asks for the project workspace.
7. The skill inspects the selected project read-only, conducts the setup
   interview, and presents one consolidated path summary.
8. Only after approval does the skill create derived storage and perform the
   existing complete validation and atomic config write.
9. The setup page continues polling and redirects when the configuration is
   ready.

## Validation And Failure Behavior

Path validation failures re-render the setup form with the submitted data-root
value and a specific error. No integration is launched. Errors distinguish a
relative path, file, unsafe link or reparse point, inaccessible existing root,
and root without a safe writable parent.

A missing or unreadable packaged skill is an installation error. Command
construction fails before terminal spawn and reports that `christag-agency`
must be reinstalled. A conflicting local `agency-setup` names the conflicting
path and asks the user to remove or rename it. Agency never silently omits
`--add-dir`, falls back to an inline copy of the skill, or overwrites local
content.

If command construction succeeds but terminal spawn fails, the waiting view may
show the integration-owned fallback command as it does today. If no valid
fallback can be constructed, the form reports the launch error directly instead
of entering a false waiting state or offering a command known to fail.

Relaunch posts the same data root and integration through the same preflight.
The waiting summary identifies the value as `Agency data root`.

## Documentation

Update the README, getting-started guide, setup-skill guide, canonical skill,
and setup surface contract to state that the browser selects the Agency data
root and the conversation selects the project workspace. Examples use
`C:\Agency` and `~/Agency` for the data root and project-oriented paths only for
the workspace question.

Historical approved specifications remain unchanged. This specification records
the newer decision and its limited supersession explicitly.

## Testing

### Setup Surface And Root Preparation

- Assert that form, picker, feedback, validation, waiting summary, and relaunch
  use Agency data-root copy and `data_root` fields with no stale project-folder
  wording.
- Accept and create a missing absolute root with a writable real parent.
- Accept an existing readable and writable real root.
- Accept an existing empty root left by an earlier failed launch without
   creating derived storage.
- Reject a relative path, file, symlink or reparse point, inaccessible root, and
  missing path without a writable real parent before launch.
- Assert that preflight creates no derived storage or config file.

### Integration Contract

- Assert that `InteractiveSetupRequest.data_root`, process cwd, and Copilot `-C`
  all receive the same strict path.
- Assert that launch and fallback commands include the packaged discovery root
  through `--add-dir` on direct and PowerShell-wrapper Windows paths.
- Assert that missing package resources and shadowing local skills fail before
  terminal spawn with actionable errors.
- Assert that a launch error followed by fallback-construction failure returns
   to the form with the launch error and does not enter the waiting state.
- Preserve existing terminal availability and wrapper safety coverage.

### Skill And Packaging Contract

- Assert that both repository discovery paths resolve to the package-owned
  canonical skill source.
- Build a wheel and recursively compare every regular file in the canonical
   `agency-setup` source tree with entries beneath the packaged discovery root.
- Assert that the guided prompt supplies the data root, asks for project
  workspace first, and no longer asks for the supplied root again.
- Assert that manual skill invocation still covers the no-root fallback path.
- Preserve path derivation, grouped override, schema-version-5, validation, and
  atomic-write contract tests.

### Verification

Run focused setup route, setup flow, integration, skill, packaging, and surface
contract tests while iterating. Run the complete Python suite before review and
again after fast-forwarding to `master`. Smoke-test `/setup` at desktop and
mobile widths to confirm the revised label, placeholder, dialog, errors, and
waiting summary fit without overlap.

## Acceptance Criteria

1. The browser path field is consistently presented as `Agency data root`.
2. A safe missing absolute root is created before launch; unsafe roots fail
   before any integration starts.
3. Copilot starts with the selected data root as its cwd and `-C` directory.
4. A clean editable or wheel installation discovers the bundled
   `agency-setup` skill without project-local or user-global setup.
5. The first guided conversational question selects the project workspace.
6. Derived storage is not created until the consolidated path summary is
   approved.
7. Setup still produces one complete, validated, atomically written
   schema-version-5 configuration.
8. A reproduction that previously emitted `Skill not found: agency-setup`
   loads the skill successfully instead.

## Rejected Alternatives

### Project The Skill Into The Data Root

Writing `<data-root>/.github/skills/agency-setup` would make cwd discovery
obvious, but it would add Copilot-specific generated files to permanent Agency
storage and require overwrite, collision, and version-lifecycle rules. The
selected design keeps runtime integration assets package-owned.

### Inline The Setup Instructions

Expanding the launch prompt to contain the full skill would avoid discovery but
duplicate a large workflow and inevitably drift from the canonical source. The
prompt remains a handoff to a packaged standard skill.

### Install A User-Global Skill

A global installation would create state outside both the selected root and the
Agency package, introduce cross-version conflicts between Agency installations,
and require cleanup or upgrade policy. First-run setup must be self-contained.