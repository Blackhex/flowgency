from __future__ import annotations

import subprocess
import sys

from agency.records.frontmatter import (
    extract_display_title,
    parse_frontmatter,
    slugify,
)


def test_parse_frontmatter_returns_meta_and_body():
    text = "---\nstatus: open\n---\n\nSome **Important Thing** here."
    meta, body = parse_frontmatter(text)
    assert meta == {"status": "open"}
    assert body == "Some **Important Thing** here."


def test_parse_frontmatter_without_frontmatter_returns_empty_meta():
    meta, body = parse_frontmatter("no frontmatter here")
    assert meta == {}
    assert body == "no frontmatter here"


def test_parse_frontmatter_with_invalid_yaml_returns_empty_meta():
    meta, body = parse_frontmatter("---\n: : :\n---\nbody")
    assert meta == {}


def test_extract_display_title_prefers_first_bold():
    assert extract_display_title("intro **Real Title.** rest", "fallback-slug") == "Real Title"


def test_extract_display_title_falls_back_to_slug():
    assert extract_display_title("", "my-slug") == "my slug"


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Suite Health: 3 Failures!") == "suite-health-3-failures"


def test_slugify_truncates_to_sixty_characters():
    assert len(slugify("x" * 200)) == 60


def test_slugify_returns_empty_string_when_nothing_survives():
    assert slugify("!!! ???") == ""


def test_importing_frontmatter_does_not_import_the_web_app():
    """The worker imports this module; it must not drag in the FastAPI layer."""
    code = (
        "import sys, agency.records.frontmatter; "
        "sys.exit(1 if 'agency.app' in sys.modules else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True)

    assert completed.returncode == 0, completed.stderr.decode()
