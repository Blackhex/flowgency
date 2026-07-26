from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

import tomli_w

from agency.projector_capabilities import PromptProjectionFormat

from .assets import PromptDocument, parse_prompt_document, prompt_source_path

if TYPE_CHECKING:
    from agency.jobs.models import PromptSnapshot


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


class PromptProjector(Protocol):
    capabilities: Any

    def project_prompt_documents(
        self,
        documents: Iterable[PromptDocument],
        destination: Path,
    ) -> tuple[PurePosixPath, ...]:
        raise NotImplementedError


def project_prompt_snapshots(
    projector: PromptProjector,
    snapshots: tuple["PromptSnapshot", ...],
    destination: Path,
) -> tuple[PurePosixPath, ...]:
    if not snapshots:
        return ()
    target = projector.capabilities.prompts_target
    format = projector.capabilities.prompt_format
    if target is None or format is None:
        return ()

    documents: list[PromptDocument] = []
    projected_paths: set[PurePosixPath] = set()
    for snapshot in snapshots:
        source = snapshot.content.encode("utf-8")
        document = parse_prompt_document(
            prompt_source_path(snapshot.name),
            source,
        )
        if document.digest != snapshot.source_digest:
            raise ValueError(
                "Private prompt snapshot digest mismatch for "
                f"{snapshot.name!r}: expected {snapshot.source_digest}, "
                f"got {document.digest}."
            )
        relative, _ = render_prompt(document, target=target, format=format)
        if relative in projected_paths:
            raise ValueError(
                "Private prompt projection path collides with another "
                f"snapshot: {relative.as_posix()}."
            )
        candidate = destination / Path(*relative.parts)
        if candidate.exists():
            raise ValueError(
                "Private prompt projection target already exists: "
                f"{relative.as_posix()}."
            )
        projected_paths.add(relative)
        documents.append(document)

    return projector.project_prompt_documents(documents, destination)


__all__ = [
    "PromptProjectionFormat",
    "PromptProjector",
    "project_prompt_snapshots",
    "render_prompt",
]