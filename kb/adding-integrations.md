# Adding a New Integration

Agency's plugin system lets you add support for any LLM tool. Each integration is a Python class that teaches Agency how to detect, read, write, and execute agents using that tool.

## What an Integration Does

| Method | Purpose |
|--------|---------|
| `detect()` | Does this agent directory belong to this tool? |
| `identity_filename()` | What file does this tool use for project instructions? |
| `parse_identity()` | Read the tool's native file and extract display name, title, emoji, and body |
| `write_identity()` | Write identity changes back in the tool's native format |
| `run()` | Execute the agent with a prompt file |
| `prompt()` | Optional: use this tool as Agency's AI backend |

## Quick Start: Write a New Integration

Create `agency/integrations/your_tool.py`:

```python
"""YourTool CLI integration."""

import shutil
import subprocess
import time
from pathlib import Path

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
    read_sidecar, write_sidecar,
)


class YourToolIntegration(BaseIntegration):
    name = "your-tool"                # Used in config.yaml
    display_name = "YourTool"         # Shown in the UI
    supports_execution = True         # Can Agency run agents with this tool?
    supports_ai_backend = False       # Can Agency use this tool for its own AI?
    detect_priority = 10              # Lower = checked first (sdk is 999)

    def identity_filename(self) -> str:
        """The file this tool uses for project instructions."""
        return "YOURTOOL.md"  # or whatever the tool reads

    def detect(self, agent_dir: Path) -> bool:
        """Check if this directory has the tool's identity file."""
        return (agent_dir / self.identity_filename()).is_file()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        """Read the identity file and extract agent metadata."""
        path = agent_dir / self.identity_filename()
        if not path.is_file():
            return None
        body = path.read_text()

        # If your tool's file supports YAML frontmatter, parse it here.
        # If not, use the sidecar file for metadata:
        meta = read_sidecar(agent_dir)
        return AgentIdentity(
            display_name=meta.get("display_name"),
            title=meta.get("title"),
            emoji=meta.get("emoji"),
            body=body,
        )

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        """Write identity back to the native file + sidecar."""
        path = agent_dir / self.identity_filename()
        path.write_text(identity.body)

        # Update sidecar metadata
        meta = read_sidecar(agent_dir)
        for key, value in [
            ("display_name", identity.display_name),
            ("title", identity.title),
            ("emoji", identity.emoji),
        ]:
            if value:
                meta[key] = value
            elif key in meta and not value:
                del meta[key]
        write_sidecar(agent_dir, meta)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        """Execute the agent with a prompt."""
        cmd = shutil.which("yourtool") or "yourtool"
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "--prompt-file", str(prompt_file)],  # adapt to your tool's CLI
                capture_output=True, text=True, timeout=timeout,
                cwd=str(agent_dir),
            )
            duration = time.monotonic() - start
            return RunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return RunResult(exit_code=124, stdout="", stderr="Timed out", duration_seconds=duration)
        except FileNotFoundError:
            raise IntegrationError(f"YourTool CLI not found. Looked for: {cmd}")


# Register at module level — this runs when the module is imported
_register(YourToolIntegration())
```

## Register the Integration

Add an import to `agency/integrations/__init__.py`:

```python
from agency.integrations.your_tool import YourToolIntegration  # noqa: E402, F401
```

That's it. Agency will now:
- Detect agents with `YOURTOOL.md` in their directory
- Show the "YourTool" badge on agent cards
- Read and write identity through your tool's native format
- Execute agents using your tool's CLI

## Key Design Decisions

### Frontmatter vs. Sidecar

If your tool's native file supports YAML frontmatter (like `CLAUDE.md` does), you can parse identity directly from frontmatter. If not (like Codex's `AGENTS.md` or Goose's `.goosehints`), use the sidecar helpers:

```python
from agency.integrations import read_sidecar, write_sidecar

meta = read_sidecar(agent_dir)  # reads .agency-meta.yaml
write_sidecar(agent_dir, meta)  # writes .agency-meta.yaml
```

The sidecar file (`.agency-meta.yaml`) stores Agency-specific metadata alongside the tool's native file.

### Detection Priority

`detect_priority` controls the order integrations are checked during auto-detection. Lower numbers are checked first. The built-in priorities:

| Priority | Integration |
|----------|-------------|
| 10 | Claude Code, Codex, Gemini, Aider, Goose |
| 100 | Default (BaseIntegration) |
| 999 | SDK (fallback — matches any `agent.md`) |
| Never | Script (explicit config only) |

Set yours to `10` to match alongside the built-in tools, or lower if your detection signal is very specific.

### AI Backend Support

If your tool can serve as Agency's AI backend (for features like summarization), implement the `prompt()` method:

```python
def prompt(self, text: str, timeout: int = 60) -> str:
    """Send a prompt and return the response text."""
    # Call your tool's API or CLI
    result = subprocess.run(
        ["yourtool", "prompt", text],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise IntegrationError(f"AI backend failed: {result.stderr}")
    return result.stdout.strip()
```

Then set `supports_ai_backend = True` and users can select your tool in Admin > Settings as the AI backend.

### No Execution (SDK-style)

If your tool doesn't have a CLI that Agency should invoke (e.g., you run agents externally and just want Agency to manage the files), set `supports_execution = False` and skip implementing `run()`.

## Testing Your Integration

Agency's test suite includes integration tests you can model from:

```bash
# Run all integration tests
.venv/bin/python -m pytest tests/test_integrations.py -v

# Run tests for a specific integration
.venv/bin/python -m pytest tests/test_integrations.py -v -k "your_tool"
```

See `tests/test_integrations.py` for the pattern — each integration is tested for registration, detection, identity parsing, and identity writing.

## Existing Integrations as Reference

| File | Tool | Good example for |
|------|------|-----------------|
| `claude_code.py` | Claude Code | Frontmatter-based identity (no sidecar needed) |
| `goose.py` | Goose | Sidecar-based identity, simple CLI execution |
| `aider.py` | Aider | Detection via config file (`.aider.conf.yml`), not identity file |
| `script.py` | Custom Script | User-provided command template with `{prompt_file}` placeholder |
| `sdk.py` | SDK | No execution, file-contract only |

## Submitting Your Integration

1. Fork [christag/agency](https://github.com/christag/agency)
2. Add your integration file, register it, and add tests
3. Update `kb/integrations.md` with your tool's row in the table
4. Open a PR with a description of what tool you're adding and how detection works

We're happy to help with your first integration — open an issue if you have questions.
