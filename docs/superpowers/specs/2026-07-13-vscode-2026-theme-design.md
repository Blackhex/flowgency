# VS Code 2026 Theme Design

## Goal

Add an Agency theme that maps the official VS Code 2026 Dark and Light
workbench palettes into Agency's existing theme system.

## Authoritative Sources

- https://github.com/microsoft/vscode/blob/main/extensions/theme-defaults/themes/2026-dark.json
- https://github.com/microsoft/vscode/blob/main/extensions/theme-defaults/themes/2026-light.json

## Theme Identity

- Theme key: `vscode-2026`
- Display name: `VS Code 2026`
- File: `agency/themes/vscode-2026.yaml`
- Replaces the incorrectly named `vscode-2016` theme.

## Dark Palette Mapping

- Main Agency background: `#191A1B`
- Agency sidebar: `#121314`
- Panel, status, terminal, and Agents panel surfaces: `#191A1B`
- Widget and notification surfaces: `#202122`
- Raised and hover surfaces: `#242526`
- Foreground: `#BFBFBF`
- Muted foreground: `#8C8C8C`
- Disabled and placeholder foreground: `#555555`
- Borders: `#2A2B2CFF`
- Input borders: `#333536`
- Primary action: `#297AA0`
- Primary hover: `#2B7DA3`
- Focus accent: `#3994BC`
- Link: `#48A0C7`
- Active list selection: `#FFFFFF22`
- List hover: `#FFFFFF14`

The main and sidebar backgrounds intentionally invert the corresponding
VS Code source roles at the user's request; all other values remain direct
2026 palette mappings.

## Light Palette Mapping

The light-mode toggle uses the corresponding official `2026-light.json`
palette:

- Editor and card background: `#FFFFFF`
- Sidebar and panel background: `#FAFAFD`
- Foreground: `#202020`
- Muted foreground: `#606060`
- Borders: `#F0F1F2FF`
- Input borders: `#D8D8D866`
- Primary action and link: `#0069CC`
- Active list selection: `#00000025`
- List hover: `#00000014`

## Integration

The CSS generator supports optional theme-level UI and input variables.
Themes that omit those variables retain the existing Agency defaults.
Desktop content uses Segoe UI-compatible 13px typography; mobile content
keeps the application's normal readable size.

## Testing

Tests verify:

1. `vscode-2026` is discovered and `vscode-2016` is absent.
2. Representative official dark and light values are loaded.
3. Generated CSS contains the UI and input variables.
4. The complete repository test suite continues to pass.
