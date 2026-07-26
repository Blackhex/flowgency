from __future__ import annotations

from pathlib import PurePosixPath

import tomli_w

from agency.projector_capabilities import PromptProjectionFormat

from .assets import PromptDocument


def render_prompt(
    document: PromptDocument,
    *,
    target: PurePosixPath,
    format: PromptProjectionFormat,
) -> tuple[PurePosixPath, bytes]:
    if format == "prompt-markdown":
        return target / f"{document.name}.prompt.md", document.source
    if format == "markdown-command":
        return target / f"{document.name}.md", document.source
    if format == "gemini-toml":
        payload = tomli_w.dumps(
            {
                "description": document.description,
                "prompt": document.body,
            }
        ).encode("utf-8")
        return target / f"{document.name}.toml", payload
    raise ValueError(f"Unsupported prompt projection format: {format}")


__all__ = ["PromptProjectionFormat", "render_prompt"]