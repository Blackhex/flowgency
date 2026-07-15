from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import yaml

from agency.blueprints.models import BlueprintInspection
from agency.fs.snapshot import AssetValidationError, capture_tree


_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SKILL_PREFIX = PurePosixPath(".agents/skills")
_FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n(.*))?\Z", re.DOTALL)


def _raise(field: str, message: str, hint: str, *, code: str = "invalid-blueprint") -> None:
    from agency.configuration.issues import ValidationIssue

    raise AssetValidationError(
        (
            ValidationIssue(
                code=code,
                scope="blueprint",
                field=field,
                message=message,
                corrective_hint=hint,
            ),
        )
    )


def _validate_key(key: str) -> None:
    if not _IDENTIFIER_PATTERN.fullmatch(key):
        _raise(
            "key",
            f"Invalid blueprint identifier: {key}",
            "Use a lowercase stable slug containing only letters, digits, and single hyphen separators.",
            code="invalid-blueprint-identifier",
        )


def _parse_skill(snapshot, skill_name: str) -> None:
    skill_path = f".agents/skills/{skill_name}/SKILL.md"
    try:
        payload = snapshot.file(skill_path).content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _raise(skill_path, f"Skill markdown must be valid UTF-8: {skill_path}.", "Rewrite SKILL.md using UTF-8 encoding.", code="invalid-skill-encoding")
        raise AssertionError("unreachable") from exc
    match = _FRONTMATTER_PATTERN.match(payload)
    if match is None:
        _raise(skill_path, f"Skill markdown frontmatter is incomplete: {skill_path}.", "Terminate the YAML frontmatter before the markdown body.", code="invalid-skill-frontmatter")
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        _raise(skill_path, f"Skill markdown frontmatter is invalid YAML: {skill_path}.", "Fix the YAML frontmatter in SKILL.md.", code="invalid-skill-frontmatter")
        raise AssertionError("unreachable") from exc
    if not isinstance(frontmatter, dict):
        _raise(skill_path, f"Skill frontmatter must be a mapping: {skill_path}.", "Set skill frontmatter to a YAML mapping with name and description.", code="invalid-skill-frontmatter")
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not _IDENTIFIER_PATTERN.fullmatch(name):
        _raise(skill_path, f"Skill name must be a lowercase stable slug: {skill_path}.", "Set frontmatter name to the exact skill directory slug.", code="invalid-skill-name")
    if name != skill_name:
        _raise(skill_path, f"Skill name must exactly match its directory: {skill_path}.", "Rename the directory or the frontmatter name so they match exactly.", code="skill-name-mismatch")
    if not isinstance(description, str):
        _raise(skill_path, f"Skill description is required: {skill_path}.", "Set a non-empty human description in SKILL.md frontmatter.", code="missing-skill-description")
    if not description.strip():
        _raise(skill_path, f"Skill description is required: {skill_path}.", "Set a non-empty human description in SKILL.md frontmatter.", code="missing-skill-description")
    if len(description) > 1024:
        _raise(skill_path, f"Skill description must be at most 1024 characters: {skill_path}.", "Shorten the skill description to 1024 characters or fewer.", code="description-too-long")


def _load_agents_md(snapshot) -> str:
    try:
        return snapshot.file("AGENTS.md").content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _raise(
            "AGENTS.md",
            "Blueprint root AGENTS.md must be valid UTF-8: AGENTS.md.",
            "Rewrite AGENTS.md using UTF-8 encoding.",
            code="invalid-blueprint-encoding",
        )
        raise AssertionError("unreachable") from exc


def _extract_title(snapshot, key: str) -> str:
    text = _load_agents_md(snapshot)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or key
    return key


def inspect_blueprint(root: Path, key: str) -> BlueprintInspection:
    library_root = Path(root).resolve()
    _validate_key(key)
    blueprint_root = library_root / key
    snapshot = capture_tree(blueprint_root)

    try:
        snapshot.file("AGENTS.md")
    except KeyError as exc:
        _raise("AGENTS.md", f"Blueprint must contain a root AGENTS.md: {key}.", "Add AGENTS.md at the blueprint root.", code="missing-blueprint-agents")
        raise AssertionError("unreachable") from exc

    skills: set[str] = set()
    for item in snapshot.files:
        if item.path.name != "SKILL.md":
            continue
        parts = item.path.parts
        if len(parts) != 4 or PurePosixPath(*parts[:2]) != _SKILL_PREFIX or parts[3] != "SKILL.md":
            _raise(item.path.as_posix(), f"Skills are only allowed at .agents/skills/<name>/SKILL.md: {item.path.as_posix()}.", "Move SKILL.md into a standard skill directory.", code="invalid-skill-location")
        skills.add(parts[2])

    for item in snapshot.files:
        if item.path.parts[:2] == _SKILL_PREFIX.parts and len(item.path.parts) >= 3:
            skill_name = item.path.parts[2]
            if not _IDENTIFIER_PATTERN.fullmatch(skill_name):
                _raise(item.path.as_posix(), f"Skill directory must be a lowercase stable slug: {item.path.as_posix()}.", "Rename the skill directory to match the Agent Skills identifier contract.", code="invalid-skill-directory")

    if not skills:
        _raise("skills", f"Blueprint must contain at least one standard Agent Skill: {key}.", "Add .agents/skills/<name>/SKILL.md to the blueprint.", code="missing-blueprint-skills")

    for skill_name in sorted(skills):
        _parse_skill(snapshot, skill_name)

    return BlueprintInspection(
        key=key,
        path=blueprint_root,
        title=_extract_title(snapshot, key),
        skills=tuple(sorted(skills)),
        snapshot=snapshot,
    )


def list_blueprints(root: Path) -> tuple[BlueprintInspection, ...]:
    library_root = Path(root).resolve()
    inspections: list[BlueprintInspection] = []
    for entry in sorted((path for path in library_root.iterdir() if path.is_dir()), key=lambda item: item.name):
        inspections.append(inspect_blueprint(library_root, entry.name))
    return tuple(inspections)


class BlueprintLibrary:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def inspect(self, key: str) -> BlueprintInspection:
        return inspect_blueprint(self.root, key)

    def list(self) -> tuple[BlueprintInspection, ...]:
        return list_blueprints(self.root)


__all__ = ["BlueprintLibrary", "inspect_blueprint", "list_blueprints"]