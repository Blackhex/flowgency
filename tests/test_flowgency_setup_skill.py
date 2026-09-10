from pathlib import Path

from flowgency.setup_assets import copilot_discovery_root


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = (
    copilot_discovery_root() / ".github" / "skills" / "flowgency-setup"
)
REPOSITORY_SKILL_DIR = REPO_ROOT / "skills" / "flowgency-setup"
DISCOVERY_SKILL_DIR = REPO_ROOT / ".github" / "skills" / "flowgency-setup"
SKILL_PATH = CANONICAL_SKILL_DIR / "SKILL.md"
DISPATCH_TEMPLATES_PATH = CANONICAL_SKILL_DIR / "references" / "dispatch-templates.md"
SETUP_KB_PATH = REPO_ROOT / "kb" / "setup-skill.md"
TEMPLATES_PATH = CANONICAL_SKILL_DIR / "references" / "templates.md"
README_PATH = REPO_ROOT / "README.md"


def test_repository_skill_paths_resolve_to_package_owned_source():
    canonical = CANONICAL_SKILL_DIR.resolve(strict=True)
    assert REPOSITORY_SKILL_DIR.resolve(strict=True) == canonical
    assert DISCOVERY_SKILL_DIR.resolve(strict=True) == canonical


def test_skill_frontmatter_and_content():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "name: flowgency-setup" in skill
    assert "schema_version: 1" in skill
    assert "flowgency:" in skill
    assert "FLOWGENCY_CONFIG" in skill
    assert "flowgency validate" in skill


def test_setup_creates_standard_global_agent_library_blueprints():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "flowgency.agent_library" in skill
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
        "After the consolidated path summary is approved, create the approved `flowgency.agent_library`"
    ) == 1, "skill contains a duplicated opening paragraph"
    # Fail if someone wrapped the replacement in a nested markdown fence
    assert "```markdown" not in skill_text, "skill contains unexpected nested markdown fence"


def test_setup_registers_explicit_instances_routines_and_memory():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "one authoritative canonical Flowgency config" in skill
    assert "flowgency.agent_library" in skill
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
        "ask for the first team project workspace as the first user-facing question"
    )
    inspection = normalized.index("inspect that workspace read-only")
    team = normalized.index(
        "ask the user to approve the team display name and stable team ID"
    )

    assert guided < workspace < inspection < team
    assert "do not ask for the data root again" in normalized


def test_setup_references_ticket_workflow_guidance():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "flowgency.workflow_library" in skill
    assert "ticket-workflow-steps.md" in skill
    assert "observation-system-steps.md" not in skill
    assert "one authoritative canonical Flowgency config" in skill
    assert "software-delivery" in skill
    assert "integration: local" in skill


def test_setup_documents_explicit_copilot_local_network_opt_in_for_tickets():
    documents = {
        "skill": SKILL_PATH.read_text(encoding="utf-8"),
        "guide": SETUP_KB_PATH.read_text(encoding="utf-8"),
        "templates": TEMPLATES_PATH.read_text(encoding="utf-8"),
    }

    for document_name, text in documents.items():
        normalized = " ".join(text.split())
        assert "allow_local_network" in text, document_name
        assert "Allow local-network access" in text, document_name
        assert "Allows connections to local services and LAN hosts, not only Flowgency." in text, document_name
        assert "ticket-local-network-required" in text, document_name
        assert "other integrations fail closed" in normalized, document_name
        assert "no automatic" in normalized.lower(), document_name

    skill_normalized = " ".join(documents["skill"].split())
    assert "before writing true" in skill_normalized
    assert "If the user declines" in documents["skill"]
    assert "do not automatically grant local-network access" in documents["skill"]


def test_manual_setup_collects_root_then_workspace_without_hidden_mode_state():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "without that complete guided context" in normalized
    assert "ask for the Flowgency data root first" in normalized
    assert "then ask for the first team project workspace" in normalized
    assert "No environment variable or hidden process state selects a mode." in skill


def test_setup_guide_describes_context_aware_team_synthesis():
    guide = SETUP_KB_PATH.read_text(encoding="utf-8")
    normalized = " ".join(guide.split())

    for phrase in (
        "summarizes concrete project facts",
        "approves the team display name and stable ID",
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
    # Actionable 4: Run step 3 uses "complete operating profiles" (not "context-aware")
    assert "context-aware operating profiles" not in guide
    # Actionable 4: Run step 2 unambiguously marks the inspection as read-only
    assert "Performs read-only inspection" in guide


def test_setup_derives_canonical_paths_from_one_data_root():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    for phrase in (
        "separate home for Flowgency-owned data",
        "existing directory or a new absolute path",
        "nearest existing parent is a writable real directory that can safely create it",
        r"C:\Flowgency",
        "~/Flowgency",
        "flowgency.agent_library = <root>/agent-library",
        "flowgency.compilation_cache = <root>/compiled-agents",
        "flowgency.memory_store = <root>/memory",
        "flowgency.prompt_store = <root>/prompts",
        "teams.<team-id>.path = <root>/teams/<team-id>",
        "teams.<team-id>.workspace_path = <project workspace>",
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
        assert "Flowgency data root" in text, document_name

    for path in (
        "C:/Flowgency/agent-library",
        "C:/Flowgency/compiled-agents",
        "C:/Flowgency/memory",
        "C:/Flowgency/prompts",
        "C:/Flowgency/teams/example",
    ):
        assert path in templates
        assert path in guide

    assert (
        templates.count("Default setup starts from one user-selected Flowgency data root.") == 1
    )
    for phrase in (
        "workspace_path is project source and execution",
        "path is Flowgency-owned team state",
        "Flowgency never loads or creates <workspace_path>/shared",
        "durable jobs live in flowgency.memory_store/.jobs",
        "operation locks live in <team.path>/locks",
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
        assert "~/Flowgency" in text, document_name
        assert "expands" in text.lower(), document_name

    assert "first question" in guide.lower()
    assert "first question" in readme.lower()
    assert "`Customize the derived storage paths?`" in guide
    # Important 1: guided/manual modes must be described; stale inspection-first ordering must be absent
    assert "After read-only project inspection, the first question asks" not in guide
    # Important 2: stale Run list that puts inspection before root/workspace must be absent
    _stale_phrase = "Asks for the " + "".join(("A", "gency")) + " data root as the first question"
    assert _stale_phrase not in guide


def test_setup_keeps_path_overrides_behind_one_grouped_review():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "Ask exactly once: `Customize the derived storage paths?`" in skill
    assert "one grouped review" in skill
    assert "Do not ask about individual storage paths in the default flow." in skill
    assert "one consolidated path summary" in skill
    assert "No derived directory or blueprint may be created before" in skill
def test_setup_skill_owns_team_naming_storage_workspaces_and_atomic_write():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split()).lower()

    for phrase in (
        "team naming",
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
    assert "team.default_integration" in normalized
    assert "initial agent instances" in normalized


def test_setup_defers_the_only_config_write_until_final_verification():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "defer creation and replacement until Section 5" in normalized
    assert "Do not write a placeholder or partial config" in normalized
    assert normalized.count("Write one complete configuration atomically.") == 1


def test_setup_persists_every_approved_workspace():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "Write every approved workspace under the team's `workspaces` list." in skill
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
    _migration_skill = "".join(("a", "gency")) + "-migration"
    assert _migration_skill not in combined
    assert "tools/migrate_agent_model.py" not in combined
    assert "schema_version: 1" in skill


def test_setup_maintains_one_authoritative_canonical_config():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "one authoritative" in skill
    assert "canonical Flowgency config" in skill
    assert "revision" in skill
    assert "atomically" in skill


def test_setup_uses_official_singleton_scheduler_cli():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "flowgency dispatch install --config" in skill
    assert "flowgency dispatch status --config" in skill
    assert "exactly one Flowgency dashboard" in skill
    assert "do not create a fallback project scheduler" in skill


def test_setup_verification_protocol_orders_atomic_write_before_revision_check():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = skill.split("## 5. Verify And Schedule", 1)[1]
    atomic = section.index("Write one complete configuration atomically.")
    revision = section.index(
        "Then parse the final config from disk and confirm it is still the revision just written."
    )
    scheduler = section.index(
        "Only when activation was approved, offer the singleton scheduler setup:"
    )

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
    validate = section.index("flowgency validate --config")
    dispatch = section.index("flowgency dispatch install")
    assert write < validate < dispatch


def test_phase_five_validates_prompt_documents():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "flowgency validate --config" in skill
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


def test_setup_summarizes_project_before_team_count_and_first_draft():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    inspection = normalized.index(
        "After the project workspace is selected, inspect that workspace read-only."
    )
    summary = normalized.index(
        "Before team design, summarize this working context in user-facing prose."
    )
    team_idx = normalized.index(
        "ask the user to approve the team display name and stable team ID"
    )
    count = normalized.index("ask for an initial positive integer agent count")
    draft = normalized.index(
        "Generate the first complete team draft with exactly that many profiles."
    )

    assert inspection < summary < team_idx < count < draft
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

    normalized = " ".join(team.split())

    # Semantic categories must be described; literal label names are not required.
    for phrase in (
        "stable name",
        "reusable blueprint",
        "display",
        "mission",
        "responsibilities",
        "handoffs",
        "rationale",
        "integration",
        "permissions",
        "routines",
        "schedules",
        "memory",
        "assumptions",
    ):
        assert phrase.lower() in normalized.lower(), f"semantic category missing: {phrase}"

    assert "exactly the approved initial count" in normalized
    assert "inspected project facts" in normalized
    assert "approved team concept" in normalized
    assert "selected integration" in normalized
    assert "Label unsupported assumptions" in normalized
    assert "None proposed" in team


def test_setup_requires_complete_semantic_profiles_with_pre_decision_check():
    """Every profile must communicate all required semantic categories in any
    clear layout; the skill must verify count and semantic-category completeness
    before presenting the team decision."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(section.split())

    # Strict-label prose must be absent
    assert "every exact label from the operating-profile template" not in normalized
    assert "Do not abbreviate, rename, merge, or omit any label" not in normalized
    assert "every required label appears exactly once in each profile" not in normalized

    # Semantic layout flexibility
    assert "any clear layout" in normalized
    assert "Closely related optional categories may be combined" in normalized
    assert "team-wide assumptions may appear once" in normalized

    # Pre-decision self-check: count and semantic-category completeness
    assert "verify that the number of complete profiles equals the approved count" in normalized
    assert "every required semantic category is identifiable in each profile" in normalized

    # Complete profiles precede the team decision, not a summary
    assert "complete profiles in the message that precedes the team-decision form" in normalized
    assert "do not replace them with compact prose or a summary" in normalized


def test_section_two_enforces_phase_barrier_before_any_other_question():
    """Regression: skill must explicitly forbid batching later-stage questions
    into team/count collection (live-session finding 2026-09-03)."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(section.split())

    assert "do not ask the user to select candidate roles or profiles" in normalized.lower()
    assert "present the first complete team draft before asking any other question" in normalized
    assert "do not ask about storage paths" in normalized.lower()
    assert "until after one consolidated team approval" in normalized
    assert "complete operating profiles, not a role-selection form" in normalized
    # Phase-barrier transition: storage path approval is explicitly gated on team approval
    assert "Obtain one consolidated team approval before continuing to storage-path approval." in normalized


def test_setup_adapts_identity_and_distinguishes_shared_roles():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "When the approved team concept clearly establishes a naming theme",
        "use domain-specific functional identities",
        "do not force a theme",
        "Agents may share a broad role",
        "responsibilities, ownership boundaries, or routines differ materially",
        "share a blueprint only when their reusable behavior and working method are genuinely the same",
    ):
        assert phrase in normalized


def test_setup_requires_clear_theme_applied_to_every_identity():
    """When the team has a clear theme, every display_name and title must
    acknowledge it; slugs and broad roles alone are insufficient."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "every `identity.display_name` and `identity.title` must visibly acknowledge it",
        "Stable slugs and broad role labels alone do not satisfy themed identity",
        "Use purely functional identities only when the theme is ambiguous or the user explicitly declines themed identities",
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


def test_section_four_permission_contract_is_scoped_to_register_instances():
    """Characterization: all four permission contract phrases exist in Section 4 specifically."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = skill.split("## 4. Register Instances", 1)[1].split("\n## ", 1)[0]
    normalized = " ".join(section.split())

    assert "For each new agent whose approved implementation responsibilities require write access" in normalized
    assert "multiple new agents may receive write" in normalized
    assert "Never infer write authority for an existing agent" in normalized
    assert "return the grant to targeted team review" in normalized


def test_setup_maps_review_profiles_only_to_existing_authority_surfaces():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "The operating profile is a conversational review model",
        "existing instance config fields",
        "existing team and memory config fields",
        "reusable behavior becomes blueprint instructions",
        "project-specific task instructions become scoped prompt documents",
        "rationale, coverage analysis, and handoff explanation remain conversational",
        "Do not persist new `mission`, `rationale`, `ownership`, `handoffs`, or `coverage` keys",
        "Keep team drafts, survivor choices, and the working context in this conversation only",
    ):
        assert phrase in normalized


def test_docs_current_ticket_contract_no_live_decision_paths():
    """Docs must reflect that ticket transitions are the current agent-only workflow.
    Decision endpoints return 410; no live human decision-execution path remains.
    Job submissions come from routine, manual, and ticket-run triggers only."""
    data_formats = (REPO_ROOT / "kb" / "data-formats.md").read_text(encoding="utf-8")
    getting_started = (REPO_ROOT / "kb" / "getting-started.md").read_text(encoding="utf-8")
    agents_md = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    # Current: only agents move tickets between workflow states
    assert "Only agents move tickets between states" in data_formats, \
        "data-formats.md must state that only agents move tickets"

    # Current: durable jobs come from routine/manual/ticket-run — decision triggers are retired
    assert "decision submissions create durable jobs" not in getting_started, \
        "getting-started.md must not claim decision submissions create durable jobs"

    # Retired: the human decision-execution path did not remain; handlers return 410
    assert "human decision-execution path remains alongside ticket workflows" not in data_formats, \
        "data-formats.md must not falsely claim the human decision-execution path remains"

    # AGENTS.md must not carry retired decide-form blocking assertions
    assert "blocks the decide form" not in agents_md, \
        "AGENTS.md must not contain retired decide-form blocking language"
    assert "Decision execution requires an explicit configured `execution_agent`" not in agents_md, \
        "AGENTS.md must not contain retired decision-execution executor requirements"


def test_setup_proposes_routines_with_recommended_cadences():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(team.split())
    for phrase in (
        "Propose useful recurring work in the first complete team draft",
        "task, prompt purpose, recommended schedule, and rationale",
        "Label each suggested cadence as a recommendation, not an existing project practice",
        "An agent with no useful recurring role may remain manual-only with a short explanation",
        "Do not add filler routines or expand permissions to accommodate a routine",
    ):
        assert phrase in normalized
    assert "`None proposed` is valid for optional emoji, routines" not in normalized


def test_setup_allows_schedule_clarification_between_draft_and_approval():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(team.split())
    draft = normalized.index("Generate the first complete team draft")
    clarify = normalized.index(
        "ask one focused question about desired recurring checks or operating cadence"
    )
    choice = normalized.index("Approve the proposed team, including its listed routines and schedules")
    assert draft < clarify < choice
    assert "after the first complete draft and before consolidated team approval" in normalized
    assert "Do not ask about storage paths, routines, schedules" not in normalized
    assert "Do not ask about storage paths, memory, or channels until after one consolidated team approval" in normalized


def test_setup_requires_explicit_manual_only_choice():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(team.split())
    for phrase in (
        "Approve the proposed team, including its listed routines and schedules",
        "Request targeted changes to profiles, routines, or schedules",
        "Choose manual-only operation for the team",
        "Manual-only operation must be an explicit user choice",
        "Mixed teams with scheduled and manual-only agents are valid",
        "A generic team approval without a visible scheduling decision is insufficient",
    ):
        assert phrase in normalized


def test_setup_docs_explain_routine_proposals_and_manual_only_choice():
    for path in (SETUP_KB_PATH, README_PATH):
        normalized = " ".join(path.read_text(encoding="utf-8").split())
        assert "proposes useful routines and recommended schedules" in normalized, path
        assert "explicitly choose manual-only operation" in normalized, path


def test_setup_separates_activation_from_schedule_approval_before_write():
    normalized = " ".join(SKILL_PATH.read_text(encoding="utf-8").split())
    approval = normalized.index("Obtain one consolidated team approval")
    activation = normalized.index("Separately ask whether to enable automatic execution")
    write = normalized.index("Write one complete configuration atomically.")
    assert approval < activation < write
    for phrase in (
        "Schedule approval alone does not authorize activation",
        "an already-running singleton scheduler can pick up enabled routines once configuration is saved",
        "Manual-only: omit routines for the new team and set `dispatch.enabled: false`",
        "Scheduled but inactive: save approved routines and set `dispatch.enabled: false`",
        "Scheduled with dispatch enabled: save approved routines and set `dispatch.enabled: true`",
        "Do not perform a second config write to activate initial schedules",
    ):
        assert phrase in normalized


def test_setup_verifies_saved_routines_against_approved_choices():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = " ".join(skill.split("## 5. Verify And Schedule", 1)[1].split())
    revision = section.index("Then parse the final config from disk")
    compare = section.index("Compare the saved configuration with the approved in-session choices")
    validate = section.index("flowgency validate --config")
    install = section.index("flowgency dispatch install --config")
    assert revision < compare < validate < install
    for phrase in (
        "owning instance, ID, scoped prompt reference, schedule, arguments, and memory selection",
        "prompt documents exist and meet the Standard Task Prompt contract",
        "dispatch enablement matches the activation decision",
        "Stop on missing or mismatched approved data",
        "Do not perform an unapproved repair write or delete approved source files",
    ):
        assert phrase in section


def test_setup_gates_scheduler_installation_and_reports_status_separately():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = " ".join(skill.split("## 5. Verify And Schedule", 1)[1].split())
    for phrase in (
        "Only when activation was approved, offer the singleton scheduler setup:",
        "Never install the scheduler solely because schedules were approved",
        "If installation is declined, fails, or its status cannot be verified",
        "do not silently change the saved config",
        "Manual-only: no routines approved; dispatch disabled",
        "Scheduled but inactive: routines saved; dispatch disabled",
        "Scheduled with dispatch enabled: routines saved; activation approved",
        "Report singleton scheduler status separately",
        "not installed, confirmed status, installation failure, or unknown status",
        "Dispatch enabled is not proof that the platform scheduler is installed or running",
        "list the saved routine schedules",
    ):
        assert phrase in section


def test_setup_docs_distinguish_schedules_activation_and_scheduler():
    guide = " ".join(SETUP_KB_PATH.read_text(encoding="utf-8").split())
    readme = " ".join(README_PATH.read_text(encoding="utf-8").split())
    for phrase in (
        "Manual-only", "Scheduled but inactive", "Scheduled with dispatch enabled",
        "compares saved routines and dispatch enablement with the approved choices",
        "scheduler status is reported separately",
    ):
        assert phrase in guide
    assert "Schedule approval does not enable automatic execution" in readme
    assert "Installing the scheduler does not create routines" in readme
