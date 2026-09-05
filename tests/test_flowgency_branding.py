from __future__ import annotations

import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops

REPO_ROOT = Path(__file__).parents[1]
STATIC_ROOT = REPO_ROOT / "flowgency" / "static"

# Bone background colour (#f3efe5 as rendered by Chromium)
_BONE = (243, 239, 229)
# Tolerance for anti-aliasing / sub-pixel blending at artwork edges
_EDGE_TOLERANCE = 16


def _png_dimensions(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", payload[16:24])


def _artwork_bbox(path: Path) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) bounding box of non-background pixels."""
    img = Image.open(path).convert("RGB")
    bg = Image.new("RGB", img.size, _BONE)
    diff = ImageChops.difference(img, bg)
    # Threshold: any channel delta above tolerance counts as artwork
    r, g, b = diff.split()
    mask = r.point(lambda v: 255 if v > _EDGE_TOLERANCE else 0)
    gm = g.point(lambda v: 255 if v > _EDGE_TOLERANCE else 0)
    bm = b.point(lambda v: 255 if v > _EDGE_TOLERANCE else 0)
    from PIL import ImageOps
    combined = ImageChops.lighter(ImageChops.lighter(mask, gm), bm)
    return combined.getbbox()


# ---------------------------------------------------------------------------
# Dimension tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SVG source integrity
# ---------------------------------------------------------------------------


def test_production_svg_and_manifest_reference_flowgency_tree():
    svg_path = STATIC_ROOT / "icon.svg"
    root = ET.parse(svg_path).getroot()
    source = svg_path.read_text(encoding="utf-8")
    manifest = json.loads((STATIC_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert root.attrib["viewBox"] == "0 0 512 512"
    # Brand palette colours (source HTML authoritative)
    for color in ("#31549f", "#d5563f", "#dda42f", "#668d70", "#365846"):
        assert color in source.lower(), f"Expected brand colour {color} in icon.svg"
    # Midpoint stop must not be present (was a spurious addition)
    assert "#4f745d" not in source.lower(), "cf-fruit must not contain spurious midpoint stop #4f745d"
    assert "flowgency-tree" in source
    assert {entry["src"] for entry in manifest["icons"]} >= {
        "/static/icon.svg",
        "/static/icon-192.png",
        "/static/icon-192-maskable.png",
        "/static/icon-512.png",
        "/static/icon-512-maskable.png",
    }


def test_svg_cf_fruit_gradient_has_two_stops():
    """cf-fruit must match source HTML exactly: 2 stops, no midpoint."""
    ns = "http://www.w3.org/2000/svg"
    tree = ET.parse(STATIC_ROOT / "icon.svg")
    gradients = [
        e for e in tree.iter(f"{{{ns}}}linearGradient") if e.get("id") == "cf-fruit"
    ]
    assert len(gradients) == 1, "Expected exactly one cf-fruit linearGradient"
    stops = gradients[0].findall(f"{{{ns}}}stop")
    colors = [s.get("stop-color") for s in stops]
    assert colors == ["#668d70", "#365846"], (
        f"cf-fruit stops must be ['#668d70', '#365846'] (source HTML), got {colors}"
    )


# ---------------------------------------------------------------------------
# Pixel-level raster checks
# ---------------------------------------------------------------------------


def test_production_pngs_are_nonblank():
    """Each raster must contain substantial non-background artwork."""
    for name in ("favicon-32.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        bbox = _artwork_bbox(STATIC_ROOT / name)
        assert bbox is not None, f"{name}: image is entirely background — artwork missing"
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        assert w > 10 and h > 10, f"{name}: artwork bounding box too small: {bbox}"


def test_production_pngs_have_background_margin():
    """Artwork must not bleed to the very edge of each raster (whole mark fits).

    Minimum margin is 1px for compact sizes and 4px for the full 512px icon,
    consistent with ~0.5–0.8% of canvas size.
    """
    checks = {
        "icon-192.png": (192, 1),
        "icon-512.png": (512, 4),
        "apple-touch-icon.png": (180, 1),
    }
    for name, (size, min_margin) in checks.items():
        bbox = _artwork_bbox(STATIC_ROOT / name)
        assert bbox is not None, f"{name}: no artwork found"
        left, top, right, bottom = bbox
        assert left >= min_margin, f"{name}: left margin {left}px < {min_margin}px"
        assert top >= min_margin, f"{name}: top margin {top}px < {min_margin}px"
        assert right <= size - min_margin, f"{name}: right edge {right}px > {size - min_margin}px"
        assert bottom <= size - min_margin, f"{name}: bottom edge {bottom}px > {size - min_margin}px"


def test_reference_png_matches_approved_dimensions():
    ref = REPO_ROOT / "docs" / "superpowers" / "specs" / "assets" / "2026-09-05-flowgency-rebrand" / "flowgency-icon.png"
    assert _png_dimensions(ref) == (512, 512), "Reference PNG must be 512×512"
    bbox = _artwork_bbox(ref)
    assert bbox is not None, "Reference PNG must contain artwork"


# ---------------------------------------------------------------------------
# Board screenshot
# ---------------------------------------------------------------------------


def test_readme_board_screenshot_matches_approved_dimensions():
    assert _png_dimensions(REPO_ROOT / "screenshots" / "flowgency-board.png") == (
        1440,
        850,
    )
