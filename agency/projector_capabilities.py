from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class ProjectorCapabilities:
    instruction_target: PurePosixPath
    skills_target: PurePosixPath
    discovers_skills: bool
    activates_selected_skill: bool
