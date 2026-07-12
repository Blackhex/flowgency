# VS Code Modern Theme Design

## Goal

Add an Agency theme that closely resembles Visual Studio Code's built-in Dark Modern theme while preserving Agency's existing light/dark toggle through a matching Light Modern palette.

## Scope

- Add one theme file: `agency/themes/vscode-modern.yaml`.
- Use the existing theme loader, CSS generator, settings selector, and dark-mode toggle unchanged.
- Add focused automated coverage for theme discovery and generated light/dark CSS.
- Do not add editor chrome, syntax highlighting, new theme properties, or template-specific overrides.

## Palette

### Dark Mode

The dark palette should evoke VS Code Dark Modern:

- Main background: `#1f1f1f`
- Sidebar and recessed surfaces: `#181818`
- Cards and elevated surfaces: neutral charcoal near `#252526`
- Inputs and secondary controls: neutral gray near `#313131`
- Borders: subtle gray near `#2b2b2b`
- Primary text: `#cccccc`
- Muted text: neutral medium gray
- Primary action and focus color: `#0078d4`
- Links and active accents: `#4daafc`

### Light Mode

The light palette should evoke VS Code Light Modern:

- Main and card backgrounds: white
- Sidebar and recessed surfaces: very light neutral gray
- Borders: subtle light gray
- Primary text: dark neutral gray
- Primary action and focus color: `#0078d4`
- Links: accessible Microsoft blue

### Logo and Status Colors

The constellation logo should use neutral workbench colors with blue nodes and lines. Existing green, amber, and red semantic meanings remain intact, with separate accessible values for dark and light modes.

## Integration

The theme file follows the same complete YAML structure as the shipped themes:

- Metadata: `name`, `description`, and `author`
- `dark` and `light` palette sections
- `logo` colors for both modes
- `status` semantic colors
- Tailwind-compatible `scale`

Because themes are discovered dynamically from `agency/themes/*.yaml`, the new theme will automatically appear in App Settings without Python or template changes.

## Error Handling

No new runtime error path is introduced. The existing theme loader continues to skip malformed YAML. Automated tests will ensure the shipped file is valid and discoverable so packaging errors are caught before release.

## Testing

Add a focused test that:

1. Loads the available themes and finds `vscode-modern`.
2. Generates CSS for the theme.
3. Verifies representative light and dark custom properties and the theme name are present.

Run the full existing test suite after implementation.
