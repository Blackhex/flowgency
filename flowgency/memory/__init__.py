from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "MemoryConflictError": ".models",
    "MemoryPublicationReceipt": ".models",
    "MemorySnapshot": ".models",
    "MemoryStage": ".models",
    "MemoryStoreError": ".models",
    "PreparedPublication": ".models",
    "ResolvedMemory": ".models",
    "MemoryPublicationError": ".publication",
    "apply_publication": ".publication",
    "finalize_publication": ".publication",
    "prepare_publication": ".publication",
    "RecoveryResult": ".recovery",
    "recover_publications": ".recovery",
    "resolve_memory_selector": ".selectors",
    "select_effective_memory": ".selectors",
    "MemoryStore": ".store",
    "ensure_memory": ".store",
    "memory_content_revision": ".store",
    "read_memory": ".store",
    "stage_memory": ".store",
    "try_save_memory": ".store",
    "store": ".store",
}


def __getattr__(name: str):
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    module = import_module(module_name, __name__)
    value = module if name == "store" else getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    "MemoryConflictError",
    "MemoryPublicationError",
    "MemoryPublicationReceipt",
    "MemorySnapshot",
    "MemoryStage",
    "MemoryStoreError",
    "MemoryStore",
    "PreparedPublication",
    "RecoveryResult",
    "ResolvedMemory",
    "apply_publication",
    "ensure_memory",
    "finalize_publication",
    "memory_content_revision",
    "prepare_publication",
    "read_memory",
    "recover_publications",
    "resolve_memory_selector",
    "select_effective_memory",
    "stage_memory",
    "try_save_memory",
]
