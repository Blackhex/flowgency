from pathlib import Path

from agency.setup_assets import copilot_discovery_root


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = (
    copilot_discovery_root() / ".github" / "skills" / "agency-setup"
)
REPOSITORY_SKILL_DIR = REPO_ROOT / "skills" / "agency-setup"
DISCOVERY_SKILL_DIR = REPO_ROOT / ".github" / "skills" / "agency-setup"
SKILL_PATH = CANONICAL_SKILL_DIR / "SKILL.md"
DISPATCH_TEMPLATES_PATH = CANONICAL_SKILL_DIR / "references" / "dispatch-templates.md"
SETUP_KB_PATH = REPO_ROOT / "kb" / "setup-skill.md"
TEMPLATES_PATH = CANONICAL_SKILL_DIR / "references" / "templates.md"
README_PATH = REPO_ROOT / "README.md"


def test_repository_skill_paths_resolve_to_package_owned_source():
    canonical = CANONICAL_SKILL_DIR.resolve(strict=True)

    assert REPOSITORY_SKILL_DIR.resolve(strict=True) == canonical
    assert DISCOVERY_SKILL_DIR.resolve(strict=True) == canonical


def test_setup_creates_standard_global_agent_library_blueprints():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "agency.agent_library" in skill
    assert "{agent_library}/{blueprint}/AGENTS.md" in skill
    assert "{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md" in skill
    assert "standard Agent Skills" in skill
    for forbidden in ("agents/{agent}/CLAUDE.md", "agents/{agent}/memory.md", "agents/{agent}/.copilot/"):
        assert forbidden not in skill


def test_setup_guidance_keeps_blueprint_skills_optional():
    documents = {
        "skill": SKILL_PATH.read_text(encoding="utf-8"),
        "guide": SETUP_KB_PATH.read_text(encoding="utf-8"),
    }
    required = (
        "Blueprints may contain zero or more standard Agent Skills.",
        "Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities.",
    )

    for document_name, text in documents.items():
        for phrase in required:
            assert phrase in text, document_name
    # Stronger checks for the canonical skill: ensure a single plain `text` fence
    skill_text = documents["skill"]
    exact_tree_fence = "```text\n{agent_library}/{blueprint}/\n`-- AGENTS.md\n```"
    assert exact_tree_fence in skill_text, "skill"
    assert skill_text.count(
        "After the consolidated path summary is approved, create the approved `agency.agent_library`"
    ) == 1, "skill contains a duplicated opening paragraph"
    # Fail if someone wrapped the replacement in a nested markdown fence
    assert "```markdown" not in skill_text, "skill contains unexpected nested markdown fence"


def test_setup_registers_explicit_instances_routines_and_memory():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "one authoritative canonical Agency config" in skill
    assert "agency.agent_library" in skill
    assert "blueprint:" in skill
    assert "integration:" in skill
    assert "routines:" in skill
    assert "prompt:" in skill
    assert "default_memory:" in skill
    assert "scope: agent" in skill
    assert "scope: routine" in skill
    assert "scope: channel" in skill
    assert "rules:" in skill
    assert "dispatch.agents" not in skill



def test_guided_setup_asks_for_workspace_before_inspection_and_team_questions():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    guided = normalized.index("Setup mode: guided-first-run.")
    workspace = normalized.index(
        "ask for the first group project workspace as the first user-facing question"
    )
    inspection = normalized.index("inspect that workspace read-only")
    team = normalized.index(
        "ask the user to approve the group display name and stable group ID"
    )

    assert guided < workspace < inspection < team
    assert "do not ask for the data root again" in normalized


def test_manual_setup_collects_root_then_workspace_without_hidden_mode_state():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "without that complete guided context" in normalized
    assert "ask for the Agency data root first" in normalized
    assert "then ask for the first group project workspace" in normalized
    assert "No environment variable or hidden process state selects a mode." in skill


def test_setup_guide_describes_context_aware_team_synthesis():
    guide = SETUP_KB_PATH.read_text(encoding="utf-8")
    normalized = " ".join(guide.split())

    for phrase in (
        "summarizes concrete project facts",
        "approves the group display name and stable ID",
        "initial positive agent count",
        "exactly that many complete operating profiles",
        "may change the count after reviewing the draft",
        "selected survivor profiles remain unchanged",
        "responsibilities and operating profiles remain materially distinct",
        "Write access follows approved implementation responsibilities",
        "one consolidated team review",
        "rationale and coverage summary remain conversational",
    ):
        assert phrase in normalized

    assert "Proposes reusable roles and asks how many agents" not in guide


def test_setup_derives_canonical_paths_from_one_data_root():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    for phrase in (
        "separate home for Agency-owned data",
        "existing directory or a new absolute path",
        "nearest existing parent is a writable real directory that can safely create it",
        r"C:\Agency",
        "~/Agency",
        "agency.agent_library = <root>/agent-library",
        "agency.compilation_cache = <root>/compiled-agents",
        "agency.memory_store = <root>/memory",
        "agency.prompt_store = <root>/prompts",
        "groups.<group-id>.path = <root>/groups/<group-id>",
        "groups.<group-id>.workspace_path = <project workspace>",
    ):
        assert phrase in skill

    # Ensure the later contiguous effective-path contract is present
    normalized = " ".join(skill.split()).lower()
    expected = (
        "resolve every effective path before creation. require that each missing effective path's nearest existing parent is a writable real directory that can safely create it"
    )
    assert expected in normalized, "Effective-path contiguous contract is missing or altered"


def test_setup_docs_present_one_data_root_default():
    templates = TEMPLATES_PATH.read_text(encoding="utf-8")
    guide = SETUP_KB_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")

    for document_name, text in {
        "templates": templates,
        "guide": guide,
        "readme": readme,
    }.items():
        assert "Agency data root" in text, document_name

    for path in (
        "C:/Agency/agent-library",
        "C:/Agency/compiled-agents",
        "C:/Agency/memory",
        "C:/Agency/prompts",
        "C:/Agency/groups/example",
    ):
        assert path in templates
        assert path in guide

    assert (
        templates.count("Default setup starts from one user-selected Agency data root.") == 1
    )
    for phrase in (
        "workspace_path is project source and execution",
        "path is Agency-owned group state",
        "Agency never loads or creates <workspace_path>/shared",
        "durable jobs live in agency.memory_store/.jobs",
        "operation locks live in <group.path>/locks",
    ):
        assert phrase in templates
    assert templates.count("## Standard Agent Skill") == 1
    for marker in (
        "Create each routine capability as a standard Agent Skill",
        "name: {skill}",
        "description: Use when {CONCRETE_TRIGGER_CONDITION}.",
    ):
        assert marker in templates

    for document_name, text in {"guide": guide, "readme": readme}.items():
        assert "~/Agency" in text, document_name
        assert "expands" in text.lower(), document_name

    assert "first question" in guide.lower()
    assert "first question" in readme.lower()
    assert "`Customize the derived storage paths?`" in guide
    # Important 1: guided/manual modes must be described; stale inspection-first ordering must be absent
    assert "After read-only project inspection, the first question asks" not in guide
    # Important 2: stale Run list that puts inspection before root/workspace must be absent
    assert "Asks for the Agency data root as the first question" not in guide


def test_setup_keeps_path_overrides_behind_one_grouped_review():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "Ask exactly once: `Customize the derived storage paths?`" in skill
    assert "one grouped review" in skill
    assert "Do not ask about individual storage paths in the default flow." in skill
    assert "one consolidated path summary" in skill
    assert "No derived directory or blueprint may be created before" in skill
def test_setup_skill_owns_group_naming_storage_workspaces_and_atomic_write():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split()).lower()

    for phrase in (
        "group naming",
        "storage paths",
        "blueprints",
        "instances",
        "routines",
        "runtime policy",
        "workspaces",
        "memory",
        "validation",
        "one atomic config write",
    ):
        assert phrase in normalized


def test_first_run_uses_exact_prompt_path_and_selected_integration():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "Authoritative config:" in normalized
    assert "use that exact path" in normalized
    assert "do not search for or choose another config" in normalized
    assert "Selected integration:" in normalized
    assert "group.default_integration" in normalized
    assert "initial agent instances" in normalized


def test_setup_defers_the_only_config_write_until_final_verification():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "defer creation and replacement until Section 5" in normalized
    assert "Do not write a placeholder or partial config" in normalized
    assert normalized.count("Write one complete configuration atomically.") == 1


def test_setup_persists_every_approved_workspace():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "Write every approved workspace under the group's `workspaces` list." in skill
    assert "workspaces:" in skill
    assert "type: ide" in skill
    assert "project_path: C:/Projects/example" in skill


def test_setup_accepts_only_canonical_configs_without_conversion_or_secondary_skills():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    kb = SETUP_KB_PATH.read_text(encoding="utf-8")
    combined = f"{skill}\n{kb}".lower()
    for phrase in (
        "accepts only the canonical config shape",
        "creates the config when absent",
        "reports validation errors",
        "never invoke another skill",
        "never scan or convert superseded authority",
    ):
        assert phrase in combined
    assert "agency-migration" not in combined
    assert "tools/migrate_agent_model.py" not in combined
    assert "schema_version: 5" in skill


def test_setup_maintains_one_authoritative_canonical_config():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "one authoritative" in skill
    assert "canonical Agency config" in skill
    assert "revision" in skill
    assert "atomically" in skill


def test_setup_uses_official_singleton_scheduler_cli():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "christag-agency dispatch install --config" in skill
    assert "christag-agency dispatch status --config" in skill
    assert "exactly one Agency dashboard" in skill
    assert "do not create a fallback project scheduler" in skill


def test_setup_verification_protocol_orders_atomic_write_before_revision_check():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = skill.split("## 5. Verify And Schedule", 1)[1]
    atomic = section.index("Write one complete configuration atomically.")
    revision = section.index(
        "Then parse the final config from disk and confirm it is still the revision just written."
    )
    scheduler = section.index("Then offer the singleton scheduler setup:")

    assert atomic < revision < scheduler


def test_setup_does_not_generate_project_scheduler_artifacts():
    combined = SKILL_PATH.read_text(encoding="utf-8") + DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")
    forbidden = [
        "agents/dispatch.ps1",
        "agents/install-dispatch.ps1",
        "agents/dispatch.sh",
        "## Windows Scheduled Task Installer Template",
        "## Systemd Timer Template",
        "## Systemd Service Template",
    ]
    for text in forbidden:
        assert text not in combined


def test_templates_define_the_task_prompt_contract():
    templates = TEMPLATES_PATH.read_text(encoding="utf-8")
    assert "## Standard Task Prompt" in templates

    # Scope assertions to the section so a matching phrase elsewhere cannot satisfy them.
    section_start = templates.index("\n## Standard Task Prompt\n") + len("\n## Standard Task Prompt\n")
    next_heading = templates.find("\n## ", section_start)
    section = templates[section_start:] if next_heading == -1 else templates[section_start:next_heading]

    assert "{agent_library}/{blueprint}/.agents/prompts/{prompt}.prompt.md" in section
    assert "```" in section  # fenced markdown block
    for required in (
        "name: {prompt}",
        "description: {ONE_LINE_PURPOSE}",
        "argument-hint: {OPTIONAL_ARGUMENT_SUMMARY}",
    ):
        assert required in section
    assert "exactly equals the file slug" in section
    assert "at most 1024 characters" in section
    assert "No keys other than" in section
    assert "non-empty" in section
    assert "lowercase letters, digits, and single hyphen separators" in section
    assert "no leading or trailing hyphen" in section
    assert "encoded as UTF-8" in section
    # Pin rules that were not previously independently testable
    assert "terminated by" in section  # frontmatter terminator rule
    assert "markdown body after the closing" in section  # non-empty body rule
    assert "when present, a string" in section  # argument-hint rule
    assert "non-whitespace-only" in section  # description whitespace rule
    assert "No other location is accepted" in section  # task prompt location rule


def test_phase_five_orders_validate_after_config_write():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = skill.split("## 5. Verify And Schedule", 1)[1].split("\n## ", 1)[0]
    write = section.index("Write one complete configuration atomically.")
    validate = section.index("christag-agency validate --config")
    dispatch = section.index("christag-agency dispatch install")
    assert write < validate < dispatch


def test_phase_five_validates_prompt_documents():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "christag-agency validate --config" in skill
    assert "prompt document" in skill
    assert "routine skill," not in skill


def test_setup_writes_routines_directly_from_assignments():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert 'at: "07:00"' in skill
    assert 'at: "21:00"' in skill
    assert "Phase 2 routine assignment" in skill
    assert "generated platform dispatch script" not in skill


def test_windows_launcher_still_resolves_real_copilot_executable():
    templates = DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")
    launcher = templates.split("## Windows Terminal Launch Script Template", maxsplit=1)[1]
    assert "Get-Command copilot -All" in launcher
    assert "-ieq '.exe'" in launcher
    assert "-EncodedCommand" in launcher
    assert "Invoke-Expression" not in launcher


def test_setup_summarizes_project_before_group_count_and_first_draft():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    inspection = normalized.index(
        "After the project workspace is selected, inspect that workspace read-only."
    )
    summary = normalized.index(
        "Before team design, summarize this working context in user-facing prose."
    )
    group = normalized.index(
        "ask the user to approve the group display name and stable group ID"
    )
    count = normalized.index("ask for an initial positive integer agent count")
    draft = normalized.index(
        "Generate the first complete team draft with exactly that many profiles."
    )

    assert inspection < summary < group < count < draft
    assert "propose three to five distinct roles" not in normalized.lower()
    assert "which proposed roles to create now" not in normalized.lower()


def test_setup_uses_one_priority_question_only_when_project_evidence_is_sparse():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "If the workspace cannot be inspected" in normalized
    assert "return to project workspace selection before proposing a team" in normalized
    assert (
        "ask exactly one focused question about near-term priorities or current pain points"
        in normalized
    )
    assert "incorporate that answer into the working context" in normalized
    assert "Do not fall back to a stock team" in normalized


def test_setup_drafts_exact_count_complete_grounded_operating_profiles():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]

    for marker in (
        "### {identity.display_name} (`{name}`)",
        "Blueprint / broad role",
        "Title / emoji",
        "Mission",
        "Responsibilities and ownership",
        "Handoffs",
        "Rationale",
        "Integration / workspace",
        "Permissions",
        "Routines and prompts",
        "Schedules",
        "Memory and channels",
        "Assumptions",
    ):
        assert marker in team

    normalized = " ".join(team.split())
    assert "exactly the approved initial count" in normalized
    assert "inspected project facts" in normalized
    assert "approved group concept" in normalized
    assert "selected integration" in normalized
    assert "Label unsupported assumptions" in normalized
    assert "None proposed" in team


def test_setup_adapts_identity_and_distinguishes_shared_roles():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "When the approved group concept clearly establishes a naming theme",
        "use domain-specific functional identities",
        "do not force a theme",
        "Agents may share a broad role",
        "responsibilities, ownership boundaries, or routines differ materially",
        "share a blueprint only when their reusable behavior and working method are genuinely the same",
    ):
        assert phrase in normalized


def test_setup_revises_count_with_verbatim_survivors_and_coherent_resynthesis():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "The user may accept the first draft, edit profiles, or replace the agent count",
        "ask which existing profiles must survive unchanged",
        "survivors outnumber the revised count",
        "reduce the survivor set or increase the count",
        "Preserve every selected survivor profile verbatim",
        "Synthesize every remaining slot from the complete working context",
        "Do not mechanically truncate the previous draft or append generic roles",
        "show the uncovered need instead of rewriting a survivor",
        "becomes the final team without a redundant second proposal",
    ):
        assert phrase in normalized


def test_setup_requires_one_consolidated_team_review_and_consistency_pass():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "Present all profiles together for one consolidated team review",
        "major project needs and their owning agents",
        "intentional shared roles",
        "handoffs and collaboration paths",
        "uncovered needs and explicit assumptions",
        "every write-enabled agent and exact writable path",
        "routine cadence and memory or channel relationships",
        "current exact agent count and preserved survivors",
        "Re-run the team-level consistency check after every count or profile change",
    ):
        assert phrase in normalized


def test_setup_derives_new_agent_write_access_from_approved_responsibilities():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "Write authority is expressed through the workspace path rule" in normalized
    assert "read and search are the baseline" in normalized
    assert "Derive write access from approved implementation responsibilities" in normalized
    assert "Multiple new agents may receive write" in normalized
    assert "exact project workspace path and explain why write is required" in normalized
    assert "Team approval includes approval of every displayed permission grant" in normalized
    assert "Never infer write authority for an existing agent" in normalized
    assert "Exactly one builder normally receives write capability" not in skill


def test_setup_maps_review_profiles_only_to_existing_authority_surfaces():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "The operating profile is a conversational review model",
        "existing instance config fields",
        "existing group and memory config fields",
        "reusable behavior becomes blueprint instructions",
        "project-specific task instructions become scoped prompt documents",
        "rationale, coverage analysis, and handoff explanation remain conversational",
        "Do not persist new `mission`, `rationale`, `ownership`, `handoffs`, or `coverage` keys",
        "Keep team drafts, survivor choices, and the working context in this conversation only",
    ):
        assert phrase in normalized


def test_docs_clarify_execution_agent_blocks_not_skips():
    """kb/data-formats.md and AGENTS.md must state that a missing, invalid, non-executable,
    or non-writable execution_agent blocks the decide form and POST until corrected — not
    that it silently creates a skipped decision. The prohibited obsolete skip row must be
    absent. The substantive-input and no-boolean-questions execution rules must be stated."""
    data_formats = (REPO_ROOT / "kb" / "data-formats.md").read_text(encoding="utf-8")
    agents_md = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    # Required: blocking language in kb/data-formats.md
    assert "blocks the decide form" in data_formats, \
        "data-formats.md must say missing/invalid execution_agent blocks the decide form"
    assert "blocked until corrected" in data_formats, \
        "data-formats.md must say the form is blocked until the executor is corrected"

    # Required: substantive non-boolean input causes execution despite all booleans declined
    assert "substantive" in data_formats, \
        "data-formats.md must describe the substantive non-boolean input rule"

    # Required: questionnaires with no boolean questions execute after validation
    assert "no `boolean` questions" in data_formats, \
        "data-formats.md must state that questionnaires with no boolean questions execute"

    # Required: blocking language in AGENTS.md
    assert "blocks the decide form" in agents_md, \
        "AGENTS.md Pipeline Relationships must say missing/invalid execution_agent blocks the decide form"

    # Prohibited: obsolete skip row implying missing executor creates a skipped decision
    assert "No writable `execution_agent` is available | `skipped`" not in data_formats, \
        "data-formats.md must not contain the inaccurate obsolete skip table row"
