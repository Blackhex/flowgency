# VS Code Modern Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a selectable Agency theme that faithfully maps VS Code Dark Modern and Light Modern colors into the existing theme system.

**Architecture:** Add one declarative YAML theme under `agency/themes/`; existing discovery and CSS generation will expose it automatically. Add one focused integration-style unit test that loads the shipped theme and verifies representative metadata plus generated light and dark CSS variables.

**Tech Stack:** Python 3.11+, pytest, PyYAML, Agency's existing YAML theme loader and CSS generator.

## Global Constraints

- Add exactly one theme file: `agency/themes/vscode-modern.yaml`.
- Do not change Python production code, templates, or the theme schema.
- Preserve the existing light/dark toggle by providing both Dark Modern and Light Modern palettes.
- Do not add editor chrome, syntax highlighting, or template-specific overrides.
- Use Microsoft blue `#0078d4` for primary actions and focus.
- Use `#1f1f1f` for the dark main background and `#181818` for the dark sidebar.

---

### Task 1: Add and Verify the VS Code Modern Theme

**Files:**
- Create: `agency/themes/vscode-modern.yaml`
- Create: `tests/test_themes.py`

**Interfaces:**
- Consumes: `agency.app.load_themes() -> dict[str, dict]`
- Consumes: `agency.app.generate_theme_css(theme: dict) -> str`
- Produces: a discovered theme under the key `vscode-modern`

- [ ] **Step 1: Write the failing theme discovery and CSS test**

Create `tests/test_themes.py`:

```python
import agency.app as app_mod


def test_vscode_modern_theme_is_discovered_and_generates_css():
    themes = app_mod.load_themes()

    assert "vscode-modern" in themes
    theme = themes["vscode-modern"]
    assert theme["name"] == "VS Code Modern"

    css = app_mod.generate_theme_css(theme)

    assert "/* Theme: VS Code Modern */" in css
    assert "--t-bg: #1f1f1f;" in css
    assert "--t-sidebar-bg: #181818;" in css
    assert "--t-primary: #0078d4;" in css
    assert "--t-bg: #ffffff;" in css
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
python -m pytest tests\test_themes.py -v
```

Expected: FAIL because `load_themes()` does not contain the `vscode-modern` key.

- [ ] **Step 3: Add the complete VS Code Modern theme**

Create `agency/themes/vscode-modern.yaml`:

```yaml
name: VS Code Modern
description: Dark Modern + Light Modern — familiar editor-focused workspace
author: agency

dark:
  # Surfaces
  bg: "#1f1f1f"
  bg_surface: "#181818"
  bg_card: "#252526"
  bg_card_hover: "#2a2d2e"
  border: "#3c3c3c"
  border_subtle: "#2b2b2b"

  # Sidebar
  sidebar_bg: "#181818"
  sidebar_text: "#9d9d9d"
  sidebar_active_bg: "rgba(0,120,212,0.18)"
  sidebar_active_text: "#ffffff"
  sidebar_section: "#6f6f6f"

  # Typography
  text: "#cccccc"
  text_heading: "#ffffff"
  text_muted: "#9d9d9d"
  text_faint: "#6f6f6f"

  # Primary action
  primary: "#0078d4"
  primary_text: "#ffffff"
  primary_hover: "#026ec1"

  # Secondary action
  secondary: "#313131"
  secondary_text: "#cccccc"
  secondary_hover: "#3c3c3c"

  # Outline action
  outline: "#0078d4"

  # Code
  code_bg: "#181818"

  # Prose
  link: "#4daafc"

light:
  # Surfaces
  bg: "#ffffff"
  bg_surface: "#f8f8f8"
  bg_card: "#ffffff"
  bg_card_hover: "#f3f3f3"
  border: "#d4d4d4"
  border_subtle: "#e5e5e5"

  # Sidebar
  sidebar_bg: "#f8f8f8"
  sidebar_text: "#616161"
  sidebar_active_bg: "rgba(0,120,212,0.10)"
  sidebar_active_text: "#005fb8"
  sidebar_section: "#8a8a8a"

  # Typography
  text: "#3b3b3b"
  text_heading: "#1f1f1f"
  text_muted: "#616161"
  text_faint: "#8a8a8a"

  # Primary action
  primary: "#0078d4"
  primary_text: "#ffffff"
  primary_hover: "#005fb8"

  # Secondary action
  secondary: "#e5e5e5"
  secondary_text: "#3b3b3b"
  secondary_hover: "#d6d6d6"

  # Outline action
  outline: "#0078d4"

  # Code
  code_bg: "#f3f3f3"

  # Prose
  link: "#005fb8"

logo:
  dark:
    bg: "#252526"
    node: "#4daafc"
    line: "#9cdcfe"
  light:
    bg: "#f8f8f8"
    bg_border: "#d4d4d4"
    node: "#0078d4"
    line: "#005fb8"

status:
  healthy: { bg_dark: "rgba(78,201,176,0.15)", text_dark: "#4ec9b0", bg_light: "#dff6f1", text_light: "#167d6b" }
  pending: { bg_dark: "rgba(206,145,120,0.18)", text_dark: "#ce9178", bg_light: "#fce8df", text_light: "#9a4d2e" }
  error: { bg_dark: "rgba(244,71,71,0.15)", text_dark: "#f44747", bg_light: "#fde2e2", text_light: "#a1260d" }

scale:
  50: "#f8f8f8"
  100: "#f3f3f3"
  200: "#d4d4d4"
  500: "#0078d4"
  600: "#005fb8"
  700: "#004578"
  800: "#252526"
  900: "#1f1f1f"
  950: "#181818"
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```powershell
python -m pytest tests\test_themes.py -v
```

Expected: `1 passed`.

- [ ] **Step 5: Run the full test suite**

Run:

```powershell
python -m pytest tests -q
```

Expected: all tests pass, with only the repository's existing intentional skips.

- [ ] **Step 6: Inspect the final patch**

Run:

```powershell
git --no-pager diff --check
git --no-pager diff -- agency/themes/vscode-modern.yaml tests/test_themes.py
```

Expected: no whitespace errors; the diff contains only the new theme and its focused test.

- [ ] **Step 7: Commit the implementation**

Run:

```powershell
git add -- agency/themes/vscode-modern.yaml tests/test_themes.py
git commit -m "feat: add VS Code Modern theme" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Expected: one commit containing the theme and regression test.
