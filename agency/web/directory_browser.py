from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class DirectoryBrowseError(ValueError):
    pass


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    path: Path


@dataclass(frozen=True)
class DirectoryListing:
    path: Path
    parent: Path
    roots: tuple[Path, ...]
    directories: tuple[DirectoryEntry, ...]


def list_directories(
    requested_path: str,
    *,
    default_path: Path,
) -> DirectoryListing:
    candidate = (
        Path(requested_path).expanduser()
        if requested_path.strip()
        else Path(default_path).expanduser()
    )
    if not candidate.is_absolute():
        raise DirectoryBrowseError("Choose an absolute directory.")
    try:
        current = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise DirectoryBrowseError("The directory does not exist.") from exc
    except OSError as exc:
        raise DirectoryBrowseError("The directory cannot be accessed.") from exc
    if not current.is_dir():
        raise DirectoryBrowseError("The selected path is not a directory.")

    try:
        children = tuple(
            DirectoryEntry(name=child.name, path=child.resolve())
            for child in current.iterdir()
            if child.is_dir()
        )
    except OSError as exc:
        raise DirectoryBrowseError("The directory cannot be read.") from exc

    directories = tuple(
        sorted(children, key=lambda item: (item.name.casefold(), item.name))
    )
    root = Path(current.anchor).resolve()
    return DirectoryListing(
        path=current,
        parent=current.parent,
        roots=(root,),
        directories=directories,
    )
