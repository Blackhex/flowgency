# Optional Workspace Launchers

Launchers are convenience frontends for configured instances. They always use the group's workspace and never create agent directories, identity files, prompt files, memory files, schedules, or configuration authority.

## Tmux Workspace Template

Generate a launcher only when the group uses a tmux workspace. Each configured instance gets a labeled pane whose working directory is `{GROUP_WORKSPACE}`. Start the selected integration CLI normally; Agency supplies projected instructions during jobs.

```bash
#!/bin/sh
set -eu
SESSION="{GROUP_KEY}-agents"
WORKSPACE="{GROUP_WORKSPACE}"
tmux new-session -d -s "$SESSION" -c "$WORKSPACE"
{TMUX_SPLITS}
{INSTANCE_COMMANDS}
tmux attach-session -t "$SESSION"
```

## Windows Terminal Launch Script Template

```powershell
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = '{GROUP_WORKSPACE}'
$Agents = @({AGENT_NAME_LITERALS})
$shell = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $shell) { $shell = (Get-Command powershell.exe -ErrorAction Stop).Source }
$terminal = Get-Command wt.exe -ErrorAction SilentlyContinue

function Get-CopilotExecutable {
  $commands = @(Get-Command copilot -All -ErrorAction SilentlyContinue)
  $executable = $commands | Where-Object {
    $_.Source -and [System.IO.Path]::GetExtension($_.Source) -ieq '.exe'
  } | Select-Object -First 1
  if ($executable) { return $executable.Source }
  throw 'GitHub Copilot CLI executable was not found on PATH.'
}

$copilotExe = Get-CopilotExecutable
$escapedCopilotExe = $copilotExe.Replace("'", "''")
$command = "& '$escapedCopilotExe' --autopilot --experimental"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))

if ($terminal) {
  $arguments = @()
  foreach ($agent in $Agents) {
    if ($arguments.Count -gt 0) { $arguments += ';' }
    $arguments += @('new-tab', '--title', $agent, '--startingDirectory', $ProjectRoot,
      $shell, '-NoExit', '-EncodedCommand', $encoded)
  }
  Start-Process -FilePath $terminal.Source -ArgumentList $arguments
  return
}

foreach ($agent in $Agents) {
  Start-Process -FilePath $shell -WorkingDirectory $ProjectRoot `
    -ArgumentList @('-NoExit', '-EncodedCommand', $encoded)
}
```

Keep instance names validated and quoted. `Get-Command copilot -All`, the `.exe` check (`-ieq '.exe'`), and `-EncodedCommand` avoid wrappers and unsafe interpolation. Keep command construction argument-safe.
