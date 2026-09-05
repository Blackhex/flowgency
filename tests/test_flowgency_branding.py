from __future__ import annotations

import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).parents[1]
STATIC_ROOT = REPO_ROOT / "flowgency" / "static"


def _png_dimensions(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", payload[16:24])


def test_production_icon_assets_have_required_dimensions():
    expected = {
        "favicon-16.png": (16, 16),
        "favicon-32.png": (32, 32),
        "apple-touch-icon.png": (180, 180),
        "icon-192.png": (192, 192),
        "icon-192-maskable.png": (192, 192),
        "icon-512.png": (512, 512),
        "icon-512-maskable.png": (512, 512),
    }
    assert {name: _png_dimensions(STATIC_ROOT / name) for name in expected} == expected


def test_production_svg_and_manifest_reference_flowgency_tree():
    svg_path = STATIC_ROOT / "icon.svg"
    root = ET.parse(svg_path).getroot()
    source = svg_path.read_text(encoding="utf-8")
    manifest = json.loads((STATIC_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert root.attrib["viewBox"] == "0 0 512 512"
    for color in ("#31549f", "#d5563f", "#dda42f", "#4f745d"):
        assert color in source.lower()
    assert "flowgency-tree" in source
    assert {entry["src"] for entry in manifest["icons"]} >= {
        "/static/icon.svg",
        "/static/icon-192.png",
        "/static/icon-192-maskable.png",
        "/static/icon-512.png",
        "/static/icon-512-maskable.png",
    }


def test_readme_board_screenshot_matches_approved_dimensions():
    assert _png_dimensions(REPO_ROOT / "screenshots" / "flowgency-board.png") == (
        1440,
        850,
    )
