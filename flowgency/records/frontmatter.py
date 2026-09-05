"""Markdown record parsing shared by the web layer and the job worker."""

from __future__ import annotations

import re

import yaml

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 60


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Returns (meta, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                return meta, parts[2].strip()
            except yaml.YAMLError:
                pass
    return {}, text


def extract_display_title(body: str | None, slug: str) -> str:
    """Extract a human-readable title from markdown body text.

    Looks for the first **bold text** in the body (not inside headings).
    Falls back to slug with hyphens replaced by spaces.
    Truncates to 120 chars if needed.
    """
    if not body:
        return slug.replace("-", " ")

    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"\*\*(.+?)\*\*", stripped)
        if m:
            title = m.group(1).rstrip(".,;:!?")
            if len(title) > 120:
                return title[:117] + "..."
            return title

    return slug.replace("-", " ")


def slugify(value: str) -> str:
    """Reduce arbitrary text to the `[a-z0-9-]{1,60}` slug alphabet."""
    collapsed = _SLUG_STRIP.sub("-", value.strip().casefold()).strip("-")
    return collapsed[:_SLUG_MAX].strip("-")
