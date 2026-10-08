# Deployment

## Running Directly

```bash
pip install -e .
flowgency serve
# or: python -m flowgency.app
```

Flowgency serves on `http://localhost:8500` by default.

## Dependencies

```
fastapi<0.116, starlette<1.0, uvicorn[standard], jinja2, markdown, pyyaml, markupsafe, python-multipart
```

All defined in `pyproject.toml`. Install with `pip install -e .`.

## Running as a systemd User Service (Linux)

A service template is provided at `flowgency.service.example`. Copy and customize it:

```bash
cp flowgency.service.example ~/.config/systemd/user/flowgency.service
# Edit the file to set your paths

systemctl --user daemon-reload
systemctl --user enable --now flowgency.service
```

### Service Management

```bash
systemctl --user status flowgency.service       # Check status
systemctl --user restart flowgency.service      # Restart after code changes
journalctl --user -u flowgency.service -f       # Stream logs
```

## Running on macOS

Run directly with `python -m flowgency.app`. For persistence, create a launchd agent or use a process manager like `brew services`.

## Platform Support

Flowgency runs on any OS with Python 3.11+:

- **Linux** — full support including systemd dispatch timers
- **macOS** — full support including launchd dispatch timers
- **Windows** — full support including Windows Task Scheduler dispatch timers

## Live Page Updates

Pages update in place from the same data they were rendered from; no manual refresh or full reload is needed.

- **Snapshot endpoint.** A live page answers its own URL with `?__live=1` as a JSON snapshot of its changing regions. Without the parameter the same URL returns the ordinary HTML page. Workflow boards and tickets keep their existing `/snapshot` transport, and setup status keeps `/setup/status`.
- **ETag and 304.** Each snapshot carries a strong SHA-256 `ETag` and `Cache-Control: private, no-cache`. The browser sends `If-None-Match`, and an unchanged page answers `304 Not Modified` after authorization and path checks have run again. Error replies are `no-store`.
- **Cadence.** A visible page reads at most once at a time, every 2 seconds; the setup status read uses 1.5 seconds. Reads never overlap, and a failed read keeps the last good content and retries at the normal cadence.
- **Hidden pages.** A hidden page cancels its read and stops polling. When it becomes visible again it reads immediately.
- **What is not covered.** A hidden tab has no polling guarantee while it stays hidden. Streaming transports (the setup terminal WebSocket) are not polled and carry no such guarantee; they keep their own connection handling. Downloads, redirects, static assets and retired HTTP 410 routes do not poll.
- **Drafts.** Held controls, open dialogs, selections, unsaved form drafts and loaded form revisions are never replaced by a snapshot; a change around them waits until they are released.

### Generated live-refresh bundle

`flowgency/static/live-refresh.js` is generated from `tools/live-refresh.js` and committed with the retained morphdom licence, `flowgency/static/morphdom.LICENSE.txt`. Both ship in the wheel; no CDN is used at runtime. After changing `tools/live-refresh.js`, regenerate and commit them:

```bash
npm install
npm run build:live
```

`build:live` bundles with esbuild (`--legal-comments=external`) and copies the installed morphdom `LICENSE` byte for byte.

## Notes

- Flowgency assumes local/trusted access. There is no built-in authentication. Use a reverse proxy (Traefik, nginx, Caddy) if you need auth.
- Use a **user-level** systemd service on Linux, not system-level. System services cannot access user home directories on immutable OSes like Fedora Kinoite.
