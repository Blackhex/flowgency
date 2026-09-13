from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from flowgency.configuration.paths import is_symlink_or_reparse
from flowgency.jobs.models import JobRecord


_SUPPORTED_SUFFIXES = {".out", ".err"}


def _is_empty_error_log(path: Path, size: int | None = None) -> bool:
    return path.suffix.lower() == ".err" and (
        path.stat().st_size if size is None else size
    ) == 0


def _path_key(path: Path) -> str:
    return str(path.resolve(strict=False)).casefold()


def _is_confined(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        return False
    return True


def _lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _has_hidden_or_reparse_ancestor(path: Path, root: Path) -> bool:
    lexical_root = _lexical_path(root)
    lexical_path = _lexical_path(path)
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError:
        return True
    current = lexical_root
    for part in relative.parts:
        current = current / part
        if current.name.startswith("."):
            return True
        if is_symlink_or_reparse(current):
            return True
    return False


def describe_log_file(path: Path, root: Path) -> tuple[str, dict[str, Any] | None]:
    candidate = Path(path)
    trusted_root = Path(root).resolve(strict=False)
    if candidate.name.startswith(".") or candidate.suffix.lower() not in _SUPPORTED_SUFFIXES:
        return "denied", None
    if _has_hidden_or_reparse_ancestor(candidate, trusted_root):
        return "denied", None
    if not _is_confined(candidate, trusted_root):
        return "denied", None
    try:
        if not candidate.exists():
            return "missing", None
        if not candidate.is_file() or is_symlink_or_reparse(candidate):
            return "denied", None
        if not os.access(candidate, os.R_OK):
            return "denied", None
        file_stat = candidate.stat()
    except OSError:
        return "denied", None
    size = file_stat.st_size
    if _is_empty_error_log(candidate, size):
        return "denied", None
    resolved = candidate.resolve(strict=False)
    return "ok", {
        "name": candidate.name,
        "path": str(resolved),
        "suffix": candidate.suffix,
        "size": size,
        "timestamp": datetime.fromtimestamp(file_stat.st_mtime),
    }


def _safe_file_entry(path: Path, root: Path) -> dict[str, Any] | None:
    status, entry = describe_log_file(path, root)
    if status != "ok":
        return None
    return entry


def _iter_date_entries(logs_root: Path):
    root = logs_root.resolve(strict=False)
    if not logs_root.exists() or is_symlink_or_reparse(logs_root):
        return
    try:
        date_dirs = sorted(logs_root.iterdir(), reverse=True)
    except OSError:
        return
    for date_dir in date_dirs:
        if date_dir.name.startswith("."):
            continue
        if is_symlink_or_reparse(date_dir) or not _is_confined(date_dir, root):
            continue
        try:
            if not date_dir.is_dir():
                continue
            children = tuple(date_dir.iterdir())
        except OSError:
            continue
        yield date_dir.name, children


def _sort_entries(entries: list[dict[str, Any]]) -> None:
    entries.sort(
        key=lambda entry: (entry["timestamp"], entry["suffix"].lower() == ".out"),
        reverse=True,
    )


def _fallback_agent_owner(filename: str, agent_names: tuple[str, ...]) -> str | None:
    stem = Path(filename).stem
    matches = [
        name
        for name in agent_names
        if stem == name or stem.startswith(f"{name}-")
    ]
    if not matches:
        return None
    longest = max(len(name) for name in matches)
    longest_matches = [name for name in matches if len(name) == longest]
    if len(longest_matches) != 1:
        return None
    return longest_matches[0]


def _known_agent_names(
    records: tuple[JobRecord, ...], configured_agent_names: tuple[str, ...]
) -> tuple[str, ...]:
    recorded_owners = {record.spec.agent_name for record in records}
    return tuple(
        sorted(
            set(configured_agent_names) | recorded_owners,
            key=lambda value: (-len(value), value),
        )
    )


def _exact_log_owners(
    records: tuple[JobRecord, ...], root: Path
) -> dict[str, tuple[str, str]]:
    exact_owners: dict[str, tuple[str, str]] = {}
    for record in records:
        for candidate_path in (record.stdout_path, record.stderr_path):
            if not candidate_path:
                continue
            candidate = _safe_file_entry(Path(candidate_path), root)
            if candidate is None:
                continue
            exact_owners[_path_key(Path(candidate["path"]))] = (
                record.spec.team_key,
                record.spec.agent_name,
            )
    return exact_owners


def log_belongs_to_agent(
    path: Path,
    logs_root: Path,
    team_id: str,
    agent_id: str,
    records: tuple[JobRecord, ...],
    configured_agent_names: tuple[str, ...],
) -> bool:
    status, entry = describe_log_file(path, logs_root)
    if status != "ok" or entry is None:
        return False
    root = Path(logs_root).resolve(strict=False)
    owner = _exact_log_owners(records, root).get(_path_key(Path(entry["path"])))
    if owner is not None:
        return owner == (team_id, agent_id)
    fallback_owner = _fallback_agent_owner(
        entry["name"],
        _known_agent_names(records, configured_agent_names),
    )
    return fallback_owner == agent_id


def collect_logs(group: dict) -> dict[str, list[dict]]:
    logs_root = Path(group["logs"])
    result: dict[str, list[dict]] = {}
    for date_key, children in _iter_date_entries(logs_root):
        entries = []
        for child in children:
            entry = _safe_file_entry(child, logs_root.resolve(strict=False))
            if entry is not None:
                entries.append(entry)
        if entries:
            _sort_entries(entries)
            result[date_key] = entries
    return result


def collect_agent_logs(
    logs_root: Path,
    team_id: str,
    agent_id: str,
    records: tuple[JobRecord, ...],
    configured_agent_names: tuple[str, ...],
) -> dict[str, list[dict]]:
    root = Path(logs_root)
    trusted_root = root.resolve(strict=False)
    agent_names = _known_agent_names(records, configured_agent_names)
    exact_owners = _exact_log_owners(records, trusted_root)

    result: dict[str, list[dict]] = {}
    for date_key, children in _iter_date_entries(root):
        entries = []
        for child in children:
            entry = _safe_file_entry(child, trusted_root)
            if entry is None:
                continue
            owner = exact_owners.get(_path_key(Path(entry["path"])))
            if owner is not None:
                if owner == (team_id, agent_id):
                    entries.append(entry)
                continue
            fallback_owner = _fallback_agent_owner(entry["name"], agent_names)
            if fallback_owner == agent_id:
                entries.append(entry)
        if entries:
            _sort_entries(entries)
            result[date_key] = entries
    return result


def log_href(
    team_id: str,
    path: str,
    *,
    agent_id: str | None = None,
    source: str | None = None,
) -> str:
    query = {"path": path}
    if agent_id is not None and source in {"activity", "logs"}:
        query.update({"agent": agent_id, "source": source})
    return f"/{quote(team_id, safe='')}/logs/view?{urlencode(query)}"


def job_log_links(
    team_id: str,
    agent_id: str,
    record: JobRecord,
    logs_root: Path,
    *,
    source: str = "activity",
) -> tuple[dict[str, str], ...]:
    if record.spec.team_key != team_id or record.spec.agent_name != agent_id:
        return ()

    links: list[dict[str, str]] = []
    for label, icon, path_value in (
        ("Output", "file-text", record.stdout_path),
        ("Error", "alert-triangle", record.stderr_path),
    ):
        if not path_value:
            continue
        candidate = _safe_file_entry(Path(path_value), logs_root.resolve(strict=False))
        if candidate is None:
            continue
        links.append(
            {
                "label": label,
                "href": log_href(team_id, candidate["path"], agent_id=agent_id, source=source),
                "icon": icon,
            }
        )
    return tuple(links)


def with_log_links(
    groups: dict[str, list[dict]],
    team_id: str,
    *,
    agent_id: str | None = None,
    source: str | None = None,
) -> dict[str, list[dict]]:
    linked: dict[str, list[dict]] = {}
    for date_key, entries in groups.items():
        linked[date_key] = [
            {
                **entry,
                "href": log_href(team_id, entry["path"], agent_id=agent_id, source=source),
            }
            for entry in entries
        ]
    return linked


__all__ = [
    "collect_agent_logs",
    "collect_logs",
    "describe_log_file",
    "job_log_links",
    "log_belongs_to_agent",
    "log_href",
    "with_log_links",
]