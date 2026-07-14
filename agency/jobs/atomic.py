"""Compatibility re-export for atomic file-write helpers."""

from agency.fs.atomic import atomic_write_bytes, atomic_write_text

__all__ = ["atomic_write_bytes", "atomic_write_text"]
