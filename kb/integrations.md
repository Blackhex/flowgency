# Integrations

Agency works with multiple LLM tools through a plugin system. Each agent uses one integration, and different agents in the same group can use different tools.

## Supported Integrations

| Integration | Identity File | How Agents Are Run |
|-------------|--------------|-------------------|
| **Claude Code** | `CLAUDE.md` | `claude --dangerously-skip-permissions -p` |
| **OpenAI Codex** | `AGENTS.md` | `codex exec --yolo` |
| **Google Gemini** | `GEMINI.md` | `gemini -p` |
| **Aider** | `CONVENTIONS.md` | `aider --message-file` |
| **Goose** | `.goosehints` | `goose run` |
| **GitHub Copilot** | `AGENTS.md` | `copilot -p --autopilot --experimental` |
| **Custom Script** | `agent.md` | Your command with `{prompt_file}` placeholder |
| **SDK** | `agent.md` | None — you run the agent externally, Agency manages the files |

## How Detection Works

Agency auto-detects which integration an agent uses by checking which identity file exists in the agent's directory. This takes priority over any config setting:

1. **Filesystem first** — check what file exists on disk
2. **Config fallback** — use the agent's `integration` field in config.yaml
3. **Group default** — use the group's `default_integration`
4. **Global default** — fall back to `claude-code`

This means an agent with `CLAUDE.md` is always handled correctly, even if the group's default integration is something else.

## Mixing Integrations

You can mix integrations within a single group. In `config.yaml`:

```yaml
groups:
  my-project:
    default_integration: claude-code
    agents:
    - researcher              # uses group default (Claude Code)
    - name: data-bot
      integration: codex      # uses Codex
    - name: runner
      integration: script     # uses a custom script
      integration_config:
        command: "./run.sh {prompt_file}"
```

## Sidecar Metadata

Tools whose native files don't support YAML frontmatter (Codex, Gemini, Aider, Goose) store Agency metadata in a `.agency-meta.yaml` sidecar file:

```yaml
display_name: Product Manager
title: Content Strategy Lead
emoji: "📦"
```

Sidecar files are created automatically when you edit identity fields in the UI.

## AI Backend

Agency itself can use an LLM for its own features (e.g., summarization). The AI backend is configured in the admin settings — choose whichever integration you want Agency to use for its own AI calls.

## Adding New Integrations

Agency's integration system is extensible. Integrations are organized by author namespace:

1. Copy `agency/integrations/_template.py` to `agency/integrations/{your-name}/your_tool.py`
2. Fill in the methods (detection, identity parsing, execution)
3. Register via the admin UI at Admin → Integrations
4. Restart the service

See the full guide: **[Contributing Integrations](contributing-integrations.md)** — includes the template walkthrough, sidecar vs. frontmatter guidance, contract tests, and submission instructions.
