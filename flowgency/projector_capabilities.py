from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal


PromptProjectionFormat = Literal[
    "prompt-markdown",
    "markdown-command",
    "gemini-toml",
]


@dataclass(frozen=True)
class ProjectorCapabilities:
    instruction_target: PurePosixPath
    skills_target: PurePosixPath
    prompts_target: PurePosixPath | None
    prompt_format: PromptProjectionFormat | None
    discovers_instructions: bool
    discovers_skills: bool
    discovers_prompts: bool
    activates_selected_skill: bool
