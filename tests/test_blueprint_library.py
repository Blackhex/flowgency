from __future__ import annotations

from pathlib import Path

import pytest

from agency.fs.snapshot import AssetValidationError, compute_source_digest
from agency.blueprints.library import BlueprintLibrary, inspect_blueprint, list_blueprints


def _write_skill(path: Path, name: str, description: str = "Review daily editorial work.") -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_bytes(
        f"---\nname: {name}\ndescription: {description}\n---\n\nRun the review.\n".encode("utf-8"),
    )


def _write_prompt(
    path: Path,
    name: str,
    *,
    description: str = "Review pull requests.",
    argument_hint: str = "Optional review focus",
    body: str = "Review the pull request.\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            f"---\nname: {name}\ndescription: {description}\n"
            f"argument-hint: {argument_hint}\n---\n\n{body}"
        ).encode("utf-8")
    )


def _write_blueprint(root: Path, key: str = "advisor") -> Path:
    blueprint = root / key
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(blueprint / ".agents" / "skills" / "daily-review", "daily-review")
    return blueprint


def test_inspect_blueprint_accepts_agents_md_without_skills(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    blueprint.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")

    inspection = inspect_blueprint(root, "advisor")

    assert inspection.key == "advisor"
    assert inspection.skills == ()
    assert tuple(item.path.as_posix() for item in inspection.snapshot.files) == (
        "AGENTS.md",
    )


def test_blueprint_inspection_includes_standard_skill(tmp_path):
    root = tmp_path / "library"
    blueprint = _write_blueprint(root)
    (blueprint / ".agents" / "agent.md").write_bytes(b"ignored")

    inspection = inspect_blueprint(root, "advisor")

    assert inspection.key == "advisor"
    assert inspection.skills == ("daily-review",)
    assert inspection.prompts == ()
    assert inspection.snapshot.digest == compute_source_digest(inspection.snapshot.files)
    assert inspection.snapshot.file(".agents/agent.md").content == b"ignored"


def test_blueprint_inspection_includes_sorted_prompt_documents_and_digest_changes(tmp_path):
    root = tmp_path / "library"
    blueprint = _write_blueprint(root)
    _write_prompt(blueprint / ".agents" / "prompts" / "zeta.prompt.md", "zeta", body="Handle zeta.\n")
    _write_prompt(blueprint / ".agents" / "prompts" / "alpha.prompt.md", "alpha", body="Handle alpha.\n")

    inspection = inspect_blueprint(root, "advisor")

    assert tuple(prompt.name for prompt in inspection.prompts) == ("alpha", "zeta")
    assert inspection.prompts[0].body == "Handle alpha.\n"
    first_digest = inspection.snapshot.digest

    _write_prompt(
        blueprint / ".agents" / "prompts" / "alpha.prompt.md",
        "alpha",
        body="Handle alpha with extra context.\n",
    )

    refreshed = inspect_blueprint(root, "advisor")

    assert refreshed.snapshot.digest != first_digest
    assert refreshed.prompts[0].body == "Handle alpha with extra context.\n"


def test_inspect_blueprint_requires_root_agents_md(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    _write_skill(blueprint / ".agents" / "skills" / "daily-review", "daily-review")

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, "advisor")


def test_inspect_blueprint_rejects_invalid_root_agents_md_utf8(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"\xff\xfe\x00bad")
    _write_skill(blueprint / ".agents" / "skills" / "daily-review", "daily-review")

    with pytest.raises(AssetValidationError) as excinfo:
        inspect_blueprint(root, "advisor")

    issue = excinfo.value.issues[0]
    assert issue.code == "invalid-blueprint-encoding"
    assert issue.field == "AGENTS.md"
    assert issue.corrective_hint == "Rewrite AGENTS.md using UTF-8 encoding."


def test_inspect_blueprint_title_falls_back_when_agents_md_has_no_heading(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("Plain blueprint body only.\n", encoding="utf-8")
    _write_skill(blueprint / ".agents" / "skills" / "daily-review", "daily-review")

    inspection = inspect_blueprint(root, "advisor")

    assert inspection.title == "advisor"


@pytest.mark.parametrize("key", ["Advisor", "advisor_bot", "advisor.", ""])
def test_inspect_blueprint_requires_stable_slug_key(tmp_path, key):
    root = tmp_path / "library"
    if key:
        _write_blueprint(root, key)

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, key)


def test_inspect_blueprint_rejects_nonstandard_skill_locations(tmp_path):
    root = tmp_path / "library"
    blueprint = _write_blueprint(root)
    rogue = blueprint / "skills" / "rogue"
    _write_skill(rogue, "rogue")

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, "advisor")


def test_inspect_blueprint_rejects_nonstandard_prompt_locations(tmp_path):
    root = tmp_path / "library"
    blueprint = _write_blueprint(root)
    _write_prompt(blueprint / ".agents" / "prompts" / "nested" / "rogue.prompt.md", "rogue")

    with pytest.raises(AssetValidationError) as excinfo:
        inspect_blueprint(root, "advisor")

    assert excinfo.value.issues[0].code == "invalid-prompt-location"


@pytest.mark.parametrize(
    ("directory_name", "frontmatter_name"),
    [("Daily-Review", "Daily-Review"), ("daily-review", "different-name"), ("daily review", "daily review")],
)
def test_inspect_blueprint_requires_exact_standard_skill_name_match(
    tmp_path, directory_name, frontmatter_name
):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(blueprint / ".agents" / "skills" / directory_name, frontmatter_name)

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, "advisor")


def test_inspect_blueprint_accepts_64_character_skill_name(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    skill_name = "a" * 64
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(blueprint / ".agents" / "skills" / skill_name, skill_name)

    inspection = inspect_blueprint(root, "advisor")

    assert inspection.skills == (skill_name,)


def test_inspect_blueprint_rejects_65_character_skill_name(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    skill_name = "a" * 65
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(blueprint / ".agents" / "skills" / skill_name, skill_name)

    with pytest.raises(AssetValidationError) as excinfo:
        inspect_blueprint(root, "advisor")

    issue = excinfo.value.issues[0]
    assert issue.code == "invalid-skill-name"
    assert issue.field == f".agents/skills/{skill_name}/SKILL.md"
    assert issue.corrective_hint == (
        "Set frontmatter name to the exact skill directory slug using 1-64 lowercase letters, digits, or hyphens."
    )


def test_inspect_blueprint_requires_skill_description(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(blueprint / ".agents" / "skills" / "daily-review", "daily-review", description="")

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, "advisor")


def test_inspect_blueprint_accepts_1024_character_skill_description(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(
        blueprint / ".agents" / "skills" / "daily-review",
        "daily-review",
        description="a" * 1024,
    )

    inspection = inspect_blueprint(root, "advisor")

    assert inspection.skills == ("daily-review",)


def test_inspect_blueprint_rejects_1025_character_skill_description(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(
        blueprint / ".agents" / "skills" / "daily-review",
        "daily-review",
        description="a" * 1025,
    )

    with pytest.raises(AssetValidationError) as excinfo:
        inspect_blueprint(root, "advisor")

    issue = excinfo.value.issues[0]
    assert issue.code == "description-too-long"
    assert issue.field == ".agents/skills/daily-review/SKILL.md"
    assert issue.corrective_hint == "Shorten the skill description to 1024 characters or fewer."


def test_inspect_blueprint_rejects_whitespace_only_skill_description(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    (blueprint / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    _write_skill(blueprint / ".agents" / "skills" / "daily-review", "daily-review", description="   ")

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, "advisor")


def test_inspect_blueprint_rejects_non_string_skill_description(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    skill_dir = blueprint / ".agents" / "skills" / "daily-review"
    skill_dir.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    (skill_dir / "SKILL.md").write_text("---\nname: daily-review\ndescription: 123\n---\n\nRun the review.\n", encoding="utf-8")

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, "advisor")


def test_inspect_blueprint_requires_utf8_skill_markdown(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    skill_dir = blueprint / ".agents" / "skills" / "daily-review"
    skill_dir.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    (skill_dir / "SKILL.md").write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(AssetValidationError):
        inspect_blueprint(root, "advisor")


def test_list_blueprints_returns_sorted_inspections(tmp_path):
    root = tmp_path / "library"
    _write_blueprint(root, "zeta")
    _write_blueprint(root, "alpha")

    inspections = list_blueprints(root)
    library = BlueprintLibrary(root)

    assert tuple(item.key for item in inspections) == ("alpha", "zeta")
    assert tuple(item.key for item in library.list()) == ("alpha", "zeta")
    assert library.inspect("alpha").key == "alpha"


def test_list_blueprints_ignores_dot_prefixed_directories(tmp_path):
    root = tmp_path / "library"
    _write_blueprint(root, "alpha")
    (root / ".git").mkdir()  # common versioned-library artifact

    inspections = list_blueprints(root)
    library = BlueprintLibrary(root)

    assert tuple(item.key for item in inspections) == ("alpha",)
    assert tuple(item.key for item in library.list()) == ("alpha",)


def test_list_blueprints_still_reports_invalid_non_dot_directory(tmp_path):
    from agency.fs.snapshot import AssetValidationError

    root = tmp_path / "library"
    _write_blueprint(root, "alpha")
    (root / "_archive").mkdir()  # bad name, should still error

    with pytest.raises(AssetValidationError):
        list_blueprints(root)
