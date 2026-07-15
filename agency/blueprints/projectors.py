from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from agency.configuration.issues import ValidationIssue
from agency.fs.snapshot import TreeSnapshot
from agency.integrations.models import ProjectorCapabilities


def _issue(code: str, field: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        scope="runtime-projector",
        field=field,
        message=message,
        corrective_hint=hint,
    )


class RuntimeProjector(Protocol):
    version: str
    capabilities: ProjectorCapabilities

    def project(self, source: TreeSnapshot, destination: Path) -> None:
        raise NotImplementedError

    def validate_output(
        self,
        source: TreeSnapshot,
        destination: Path,
    ) -> tuple[ValidationIssue, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class StaticRuntimeProjector:
    version: str
    capabilities: ProjectorCapabilities

    def _mapped_paths(self, source: TreeSnapshot) -> dict[PurePosixPath, bytes]:
        mapped: dict[PurePosixPath, bytes] = {}
        for item in source.files:
            if item.path == PurePosixPath("AGENTS.md"):
                mapped[self.capabilities.instruction_target] = item.content
                continue
            if item.path.parts[:2] == (".agents", "skills"):
                suffix = item.path.relative_to(PurePosixPath(".agents/skills"))
                mapped[self.capabilities.skills_target / suffix] = item.content
        return mapped

    def project(self, source: TreeSnapshot, destination: Path) -> None:
        mapped = self._mapped_paths(source)
        for relative, content in mapped.items():
            target = destination / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def validate_output(
        self,
        source: TreeSnapshot,
        destination: Path,
    ) -> tuple[ValidationIssue, ...]:
        expected = self._mapped_paths(source)
        issues: list[ValidationIssue] = []
        actual: set[PurePosixPath] = set()
        for root in (self.capabilities.instruction_target, self.capabilities.skills_target):
            candidate = destination / Path(*root.parts)
            if candidate.is_file():
                actual.add(root)
            elif candidate.is_dir():
                for child in candidate.rglob("*"):
                    if child.is_file():
                        actual.add(PurePosixPath(*child.relative_to(destination).parts))
        expected_paths = set(expected)
        for relative, content in expected.items():
            target = destination / Path(*relative.parts)
            if not target.is_file():
                issues.append(
                    _issue(
                        "projector-missing-path",
                        relative.as_posix(),
                        f"Projected runtime file is missing: {relative.as_posix()}.",
                        "Rebuild the runtime projection and ensure every expected file is written.",
                    )
                )
                continue
            if target.read_bytes() != content:
                issues.append(
                    _issue(
                        "projector-byte-mismatch",
                        relative.as_posix(),
                        f"Projected runtime file does not match source bytes: {relative.as_posix()}.",
                        "Rewrite the projection without mutating instruction or skill file bytes.",
                    )
                )
        for relative in sorted(actual - expected_paths, key=lambda value: value.as_posix()):
            issues.append(
                _issue(
                    "projector-unexpected-path",
                    relative.as_posix(),
                    f"Projected runtime contains an unexpected file: {relative.as_posix()}.",
                    "Remove files that are not part of the runtime projection contract.",
                )
            )
        return tuple(issues)


PROJECTORS: dict[str, StaticRuntimeProjector] = {
    "copilot": StaticRuntimeProjector(
        version="v1",
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("AGENTS.md"),
            skills_target=PurePosixPath(".agents/skills"),
            discovers_skills=True,
            activates_selected_skill=True,
        ),
    ),
    "claude-code": StaticRuntimeProjector(
        version="v1",
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("CLAUDE.md"),
            skills_target=PurePosixPath(".claude/skills"),
            discovers_skills=False,
            activates_selected_skill=False,
        ),
    ),
    "gemini": StaticRuntimeProjector(
        version="v1",
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("GEMINI.md"),
            skills_target=PurePosixPath(".agents/skills"),
            discovers_skills=False,
            activates_selected_skill=False,
        ),
    ),
}


def get_projector(integration: str) -> StaticRuntimeProjector:
    try:
        return PROJECTORS[integration]
    except KeyError as exc:
        raise KeyError(f"No runtime projector registered for integration: {integration}") from exc


__all__ = [
    "PROJECTORS",
    "RuntimeProjector",
    "StaticRuntimeProjector",
    "get_projector",
]