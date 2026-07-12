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
