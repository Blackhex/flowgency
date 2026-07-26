from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import unicodedata

from agency.fs.atomic import atomic_write_bytes
from agency.fs.locks import exclusive_lock

from .assets import PromptDocument, parse_prompt_document, prompt_source_path


_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LOWER_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True)
class StoredPrompt:
    document: PromptDocument
    path: Path


class PromptConflictError(RuntimeError):
    pass


class PromptNotFoundError(RuntimeError):
    pass


class PromptStore:
    def __init__(self, root: Path):
        configured = Path(root).expanduser()
        if not configured.is_absolute():
            configured = Path.cwd() / configured
        self.root = Path(os.path.abspath(str(configured)))

    def path(self, group: str, instance: str, name: str) -> Path:
        group_slug = _validate_slug("group", group)
        instance_slug = _validate_slug("instance", instance)
        name_slug = _validate_slug("prompt", name)
        candidate = Path(
            os.path.abspath(
                str(
                    self.root
                    / group_slug
                    / instance_slug
                    / f"{name_slug}.prompt.md"
                )
            )
        )
        _require_contained(self.root, candidate, label="prompt")
        return candidate

    def read(self, group: str, instance: str, name: str) -> StoredPrompt:
        path = self.path(group, instance, name)
        with self._namespace_lock(group, instance):
            payload = _read_prompt_file(self.root, path)
        document = parse_prompt_document(prompt_source_path(name), payload)
        return StoredPrompt(document=document, path=path)

    def create(
        self,
        group: str,
        instance: str,
        name: str,
        payload: bytes,
    ) -> StoredPrompt:
        path = self.path(group, instance, name)
        document = parse_prompt_document(prompt_source_path(name), payload)
        with self._namespace_lock(group, instance):
            self._ensure_namespace_directory(group, instance)
            if path.exists():
                _validate_safe_leaf(path, label="prompt")
                raise PromptConflictError(f"prompt already exists: {path}")
            atomic_write_bytes(path, payload)
        return StoredPrompt(document=document, path=path)

    def update(
        self,
        group: str,
        instance: str,
        name: str,
        *,
        expected_digest: str,
        payload: bytes,
    ) -> StoredPrompt:
        path = self.path(group, instance, name)
        expected = _validate_digest(expected_digest)
        document = parse_prompt_document(prompt_source_path(name), payload)
        with self._namespace_lock(group, instance):
            current = _read_prompt_file(self.root, path)
            current_digest = hashlib.sha256(current).hexdigest()
            if current_digest != expected:
                raise PromptConflictError("prompt changed; reload and retry")
            atomic_write_bytes(path, payload)
        return StoredPrompt(document=document, path=path)

    def delete(
        self,
        group: str,
        instance: str,
        name: str,
        *,
        expected_digest: str,
    ) -> StoredPrompt:
        path = self.path(group, instance, name)
        expected = _validate_digest(expected_digest)
        with self._namespace_lock(group, instance):
            payload = _read_prompt_file(self.root, path)
            current_digest = hashlib.sha256(payload).hexdigest()
            if current_digest != expected:
                raise PromptConflictError("prompt changed; reload and retry")
            document = parse_prompt_document(prompt_source_path(name), payload)
            path.unlink()
        return StoredPrompt(document=document, path=path)

    def copy_namespace(
        self,
        source_group: str,
        source_instance: str,
        target_group: str,
        target_instance: str,
        *,
        registered: tuple[tuple[str, str], ...],
    ) -> tuple[Path, ...]:
        _validate_slug("group", source_group)
        _validate_slug("instance", source_instance)
        _validate_slug("group", target_group)
        _validate_slug("instance", target_instance)
        items = _validate_registered(registered)

        lock_paths = self._sorted_namespace_lock_paths(
            (source_group, source_instance),
            (target_group, target_instance),
        )
        with self._acquire_locks(lock_paths):
            self._require_namespace_directory(source_group, source_instance)
            self._ensure_namespace_directory(target_group, target_instance)
            staged: list[tuple[Path, bytes]] = []
            for name, expected_digest in items:
                source_path = self.path(source_group, source_instance, name)
                payload = _read_prompt_file(self.root, source_path)
                source_document = parse_prompt_document(
                    prompt_source_path(name),
                    payload,
                )
                if source_document.digest != expected_digest:
                    raise PromptConflictError("prompt changed; reload and retry")
                target_path = self.path(target_group, target_instance, name)
                if target_path.exists():
                    _validate_safe_leaf(target_path, label="prompt")
                    raise PromptConflictError(
                        f"prompt already exists: {target_path}"
                    )
                staged.append((target_path, payload))

            created: list[Path] = []
            try:
                for target_path, payload in staged:
                    atomic_write_bytes(target_path, payload)
                    created.append(target_path)
            except Exception as exc:
                rollback_error = _rollback_created_paths(created)
                if rollback_error is not None:
                    raise RuntimeError(
                        "namespace copy failed and rollback failed: "
                        f"{exc}; rollback error: {rollback_error}"
                    ) from exc
                raise RuntimeError(
                    f"namespace copy failed and rolled back: {exc}"
                ) from exc
            return tuple(created)

    def delete_namespace(
        self,
        group: str,
        instance: str,
        *,
        registered: tuple[tuple[str, str], ...],
    ) -> tuple[Path, ...]:
        _validate_slug("group", group)
        _validate_slug("instance", instance)
        items = _validate_registered(registered)

        with self._namespace_lock(group, instance):
            self._require_namespace_directory(group, instance)
            staged: list[tuple[Path, bytes, PromptDocument]] = []
            for name, expected_digest in items:
                path = self.path(group, instance, name)
                payload = _read_prompt_file(self.root, path)
                document = parse_prompt_document(prompt_source_path(name), payload)
                if document.digest != expected_digest:
                    raise PromptConflictError("prompt changed; reload and retry")
                staged.append((path, payload, document))

            deleted: list[Path] = []
            try:
                for path, _payload, _document in staged:
                    path.unlink()
                    deleted.append(path)
            except Exception as exc:
                rollback_error = _restore_deleted_prompts(staged, deleted)
                if rollback_error is not None:
                    raise RuntimeError(
                        "namespace delete failed and restore failed: "
                        f"{exc}; restore error: {rollback_error}"
                    ) from exc
                raise RuntimeError(
                    f"namespace delete failed and restored deleted prompts: {exc}"
                ) from exc
            return tuple(deleted)

    @contextmanager
    def _namespace_lock(self, group: str, instance: str):
        key = self._namespace_key(group, instance)
        lock_path = self._lock_path_for_key(key)
        with exclusive_lock(lock_path, wait=True):
            yield

    @contextmanager
    def _acquire_locks(self, lock_paths: list[Path]):
        with ExitStack() as stack:
            for lock_path in lock_paths:
                stack.enter_context(exclusive_lock(lock_path, wait=True))
            yield

    def _namespace_key(self, group: str, instance: str) -> str:
        group_slug = _validate_slug("group", group)
        instance_slug = _validate_slug("instance", instance)
        return f"namespace:{group_slug}:{instance_slug}"

    def _sorted_namespace_lock_paths(
        self,
        *namespaces: tuple[str, str],
    ) -> list[Path]:
        # One namespace lock domain covers all prompt operations on an instance.
        lock_paths = {
            self._lock_path_for_key(self._namespace_key(group, instance))
            for group, instance in namespaces
        }
        return sorted(lock_paths, key=lambda item: _path_key(item))

    def _lock_path_for_key(self, key: str) -> Path:
        _ensure_directory_chain(self.root, [], label="prompts")
        lock_root = _ensure_directory_chain(self.root, [".locks"], label="locks")
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        lock_path = lock_root / f"{digest}.lock"
        _require_contained(self.root, lock_path.resolve(strict=False), label="locks")
        _validate_safe_leaf(lock_path, label="locks")
        return lock_path

    def _ensure_namespace_directory(self, group: str, instance: str) -> Path:
        group_slug = _validate_slug("group", group)
        instance_slug = _validate_slug("instance", instance)
        return _ensure_directory_chain(
            self.root,
            [group_slug, instance_slug],
            label="prompts",
        )

    def _require_namespace_directory(self, group: str, instance: str) -> Path:
        group_slug = _validate_slug("group", group)
        instance_slug = _validate_slug("instance", instance)
        namespace_path = self.root / group_slug / instance_slug
        exists = _validate_existing_directory_chain(
            self.root,
            namespace_path,
            label="prompts",
        )
        if not exists:
            raise PromptNotFoundError(f"prompt namespace not found: {namespace_path}")
        return namespace_path


def _validate_registered(
    registered: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    if not isinstance(registered, tuple):
        raise TypeError("registered prompts must be a tuple")
    seen: set[str] = set()
    validated: list[tuple[str, str]] = []
    for item in registered:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("registered prompts must include (name, digest) pairs")
        name = _validate_slug("prompt", item[0])
        digest = _validate_digest(item[1])
        if name in seen:
            raise ValueError(f"registered prompt names must be unique: {name}")
        seen.add(name)
        validated.append((name, digest))
    return tuple(validated)


def _validate_slug(kind: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{kind} must be a string")
    if not (1 <= len(value) <= 64) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{kind} must be a lowercase stable slug with 1-64 characters"
        )
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value or normalized.casefold() != value:
        raise ValueError(f"{kind} must be a lowercase stable slug")
    if value.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{kind} uses a reserved Windows basename")
    return value


def _validate_digest(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("expected digest must be a string")
    if _LOWER_HEX_64_RE.fullmatch(value) is None:
        raise ValueError("expected digest must be 64 lowercase hex characters")
    return value


def _read_regular_file(path: Path) -> bytes:
    _validate_safe_leaf(path, label="prompt")
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise PromptNotFoundError(f"prompt not found: {path}") from exc
    if not stat.S_ISREG(stat_result.st_mode):
        raise ValueError(f"prompt path must be a regular file: {path}")
    return path.read_bytes()


def _read_prompt_file(root: Path, path: Path) -> bytes:
    exists = _validate_existing_directory_chain(root, path.parent, label="prompts")
    if not exists:
        raise PromptNotFoundError(f"prompt not found: {path}")
    return _read_regular_file(path)


def _validate_safe_leaf(path: Path, *, label: str) -> None:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return
    if _stat_is_symlink_or_reparse(stat_result):
        raise ValueError(f"unsafe {label} path: {path}")


def _ensure_directory_chain(root: Path, parts: list[str], *, label: str) -> Path:
    current = _ensure_real_directory(root, label=label, create=True)
    for name in parts:
        current = _ensure_child_directory(current, name, label=label)
    return current


def _validate_existing_directory_chain(root: Path, path: Path, *, label: str) -> bool:
    root = Path(root)
    path = Path(path)
    _require_contained(root, path.resolve(strict=False), label=label)
    try:
        current = _ensure_real_directory(root, label=label)
    except ValueError:
        raise
    except FileNotFoundError:
        return False

    if _path_key(path) == _path_key(root):
        return True

    for child in _paths_from_root(root, path):
        _validate_safe_leaf(child, label=label)
        try:
            stat_result = child.lstat()
        except FileNotFoundError:
            return False
        if _stat_is_symlink_or_reparse(stat_result):
            raise ValueError(f"unsafe {label} directory: {child}")
        if not stat.S_ISDIR(stat_result.st_mode):
            raise ValueError(f"{label} path is not a directory: {child}")
        current = child
    return True


def _ensure_child_directory(parent: Path, name: str, *, label: str) -> Path:
    child = parent / name
    _validate_safe_leaf(child, label=label)
    try:
        child.lstat()
    except FileNotFoundError:
        _ensure_real_directory(parent, label=label)
        try:
            child.mkdir()
        except FileExistsError:
            pass
    return _ensure_real_directory(child, label=label)


def _ensure_real_directory(path: Path, *, label: str, create: bool = False) -> Path:
    if create:
        path.mkdir(parents=True, exist_ok=True)
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"missing {label} directory: {path}") from exc
    if _stat_is_symlink_or_reparse(stat_result):
        raise ValueError(f"unsafe {label} directory: {path}")
    if not stat.S_ISDIR(stat_result.st_mode):
        raise ValueError(f"{label} path is not a directory: {path}")
    return path


def _require_contained(root: Path, candidate: Path, *, label: str) -> None:
    root_key = Path(_path_key(root))
    candidate_key = Path(_path_key(candidate))
    if candidate_key == root_key:
        return
    if root_key not in candidate_key.parents:
        raise ValueError(f"{label} path escapes store root")


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(Path(path))))


def _paths_from_root(root: Path, path: Path) -> list[Path]:
    chain: list[Path] = []
    current = Path(path)
    root_key = _path_key(root)
    while _path_key(current) != root_key:
        chain.append(current)
        parent = current.parent
        if _path_key(parent) == _path_key(current):
            raise ValueError("prompt path escapes store root")
        current = parent
    chain.reverse()
    return chain


def _stat_is_symlink_or_reparse(stat_result: os.stat_result) -> bool:
    file_attributes = getattr(stat_result, "st_file_attributes", 0) or 0
    return bool(
        stat.S_ISLNK(stat_result.st_mode)
        or (
            file_attributes
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


def _rollback_created_paths(created: list[Path]) -> Exception | None:
    try:
        for path in reversed(created):
            try:
                _validate_safe_leaf(path, label="prompt")
                if path.exists():
                    path.unlink()
            except FileNotFoundError:
                continue
        return None
    except Exception as rollback_error:  # noqa: BLE001
        return rollback_error


def _restore_deleted_prompts(
    staged: list[tuple[Path, bytes, PromptDocument]],
    deleted: list[Path],
) -> Exception | None:
    payload_by_path = {path: payload for path, payload, _document in staged}
    try:
        for path in reversed(deleted):
            atomic_write_bytes(path, payload_by_path[path])
        return None
    except Exception as restore_error:  # noqa: BLE001
        return restore_error


__all__ = [
    "PromptConflictError",
    "PromptNotFoundError",
    "PromptStore",
    "StoredPrompt",
]
