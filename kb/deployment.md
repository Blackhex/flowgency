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

## Notes

- Flowgency assumes local/trusted access. There is no built-in authentication. Use a reverse proxy (Traefik, nginx, Caddy) if you need auth.
- Use a **user-level** systemd service on Linux, not system-level. System services cannot access user home directories on immutable OSes like Fedora Kinoite.
