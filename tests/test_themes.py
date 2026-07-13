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
    assert ".nav-item.active { color: var(--t-sidebar-active-text, #fff) !important;" in css


def test_vscode_2026_theme_is_discovered_and_generates_css():
    themes = app_mod.load_themes()

    assert "vscode-2026" in themes
    assert "vscode-2016" not in themes
    theme = themes["vscode-2026"]
    assert theme["name"] == "VS Code 2026"
    assert theme["dark"]["bg"] == "#191a1b"
    assert theme["dark"]["bg_surface"] == "#191a1b"
    assert theme["dark"]["bg_card"] == "#191a1b"
    assert theme["dark"]["border"] == "#2a2b2cff"
    assert theme["dark"]["sidebar_bg"] == "#121314"
    assert theme["dark"]["sidebar_hover_bg"] == "#ffffff14"
    assert theme["dark"]["sidebar_active_bg"] == "#ffffff22"
    assert theme["dark"]["text"] == "#bfbfbf"
    assert theme["dark"]["input_bg"] == "#191a1b"
    assert theme["dark"]["input_border"] == "#333536"
    assert theme["logo"]["dark"]["bg"] == "#191a1b"
    assert theme["ui"]["font_family"] == '"Segoe WPC", "Segoe UI", system-ui, sans-serif'

    css = app_mod.generate_theme_css(theme)

    assert "/* Theme: VS Code 2026 */" in css
    assert "--t-bg: #191a1b;" in css
    assert "--t-sidebar-bg: #121314;" in css
    assert "--t-sidebar-hover-bg: #ffffff14;" in css
    assert "--t-primary: #297aa0;" in css
    assert "--t-input-bg: #191a1b;" in css
    assert "background-color: var(--t-input-bg, var(--t-code-bg)) !important;" in css
    assert "--t-bg: #ffffff;" in css
    assert '--t-ui-font-family: "Segoe WPC", "Segoe UI", system-ui, sans-serif;' in css
    assert "--t-ui-main-font-size: 13px;" in css
    assert "--t-ui-radius-lg: 2px;" in css
    assert ".nav-item:hover { color: var(--t-sidebar-active-text) !important; background: var(--t-sidebar-hover-bg, rgba(255,255,255,0.06)) !important; }" in css
    assert "@media (min-width: 768px) {\n  main { font-size: var(--t-ui-main-font-size, 1.0625rem); }\n}" in css
    assert "html.dark body.text-gray-900 { color: var(--t-text) !important; }" in css
    assert ".nav-item.active { color: var(--t-sidebar-active-text, #fff) !important;" in css
