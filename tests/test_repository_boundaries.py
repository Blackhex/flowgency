from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).parents[1]
PROHIBITED_TERMS = (
    "".join(("v", "2")),
    "".join(("leg", "acy")),
)
COORDINATION_PATHS = (
    ":(exclude)docs/superpowers/plans/2026-07-18-*.md",
    ":(exclude)docs/superpowers/plans/2026-08-01-*.md",
    ":(exclude)docs/superpowers/specs/2026-07-18-first-run-setup-launcher-design.md",
)
CONTENT_SCAN_EXCLUSIONS = (
    ":(exclude)flowgency/static/lucide.min.js",
    ":(exclude)package-lock.json",
)

# A generated SRI subresource-integrity digest ("sha512-<base64>") is opaque
# binary data, not application terminology: its base64 alphabet can contain
# any prohibited term as a coincidental substring. Match the same shape npm
# actually writes so only a genuine digest payload is exempted below.
_SRI_TOKEN = re.compile(r"^sha(?:1|256|384|512)-[A-Za-z0-9+/]+={0,2}$")


def _is_valid_integrity(value: str) -> bool:
    tokens = value.split()
    return bool(tokens) and all(_SRI_TOKEN.match(token) for token in tokens)


def _lockfile_meaningful_text(raw: str) -> str:
    """Serialize lockfile JSON with only valid SRI integrity values blanked.

    Package names, paths, URLs, licenses, and every other field remain
    subject to the term scan; only a validly-shaped digest payload under an
    "integrity" key is exempted.
    """

    def scrub(value):
        if isinstance(value, dict):
            scrubbed = {}
            for key, inner in value.items():
                if key == "integrity" and isinstance(inner, str) and _is_valid_integrity(inner):
                    scrubbed[key] = ""
                else:
                    scrubbed[key] = scrub(inner)
            return scrubbed
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return json.dumps(scrub(json.loads(raw)))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.mark.parametrize("term", PROHIBITED_TERMS)
def test_tracked_tree_omits_prohibited_terms(repo_root: Path, term: str):
    completed = subprocess.run(
        [
            "git",
            "grep",
            "-Iil",
            term,
            "--",
            ".",
            *COORDINATION_PATHS,
            *CONTENT_SCAN_EXCLUSIONS,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout
    lockfile_text = _lockfile_meaningful_text(
        (repo_root / "package-lock.json").read_text(encoding="utf-8")
    )
    assert term not in lockfile_text.lower()


@pytest.mark.parametrize("term", PROHIBITED_TERMS)
def test_lockfile_scan_ignores_valid_integrity_digests_only(term: str):
    fixture = json.dumps(
        {
            "packages": {
                "node_modules/example": {
                    "version": "1.0.0",
                    "integrity": f"sha512-AAAA{term}BBBB==",
                }
            }
        }
    )
    assert term not in _lockfile_meaningful_text(fixture).lower()


@pytest.mark.parametrize("term", PROHIBITED_TERMS)
def test_lockfile_scan_still_flags_meaningful_metadata(term: str):
    fixture = json.dumps(
        {
            "packages": {
                f"node_modules/{term}-package": {
                    "version": "1.0.0",
                    "integrity": "sha512-AAAABBBB==",
                }
            }
        }
    )
    assert term in _lockfile_meaningful_text(fixture).lower()


@pytest.mark.parametrize("term", PROHIBITED_TERMS)
def test_lockfile_scan_still_flags_invalid_integrity_text(term: str):
    fixture = json.dumps(
        {
            "packages": {
                "node_modules/example": {
                    "version": "1.0.0",
                    "integrity": f"not-a-real-digest-{term}",
                }
            }
        }
    )
    assert term in _lockfile_meaningful_text(fixture).lower()


def test_tracked_paths_omit_prohibited_terms(repo_root: Path):
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_paths = [
        path
        for path in completed.stdout.splitlines()
        if not path.startswith("docs/superpowers/plans/2026-07-18-")
        and path
        != "docs/superpowers/specs/2026-07-18-first-run-setup-launcher-design.md"
    ]
    lowered = "\n".join(tracked_paths).lower()
    for term in PROHIBITED_TERMS:
        assert term not in lowered


def test_application_does_not_construct_project_local_shared_paths(repo_root: Path):
    import re

    patterns = (
        re.compile(r'group\.path\s*/\s*["\']shared["\']'),
        re.compile(r'\[["\']shared["\']\]'),
        re.compile(r'/\s*["\']shared["\']'),
    )
    matches = []
    for path in (repo_root / "flowgency").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                matches.append(f"{path.relative_to(repo_root)}:{line_number}:{line}")
    assert not matches, "\n".join(matches)
