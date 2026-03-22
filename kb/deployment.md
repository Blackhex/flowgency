# Deployment

## Running Directly

```bash
pip install -e .
python -m agency.app
# or: agency --port 8500 --host 0.0.0.0
```

Agency serves on `http://localhost:8500` by default.

## Dependencies

```
fastapi<0.116, starlette<1.0, uvicorn[standard], jinja2, markdown, pyyaml, markupsafe, python-multipart
```

All defined in `pyproject.toml`. Install with `pip install -e .`.

## Running as a systemd User Service (Linux)

A service template is provided at `agency.service.example`. Copy and customize it:

```bash
cp agency.service.example ~/.config/systemd/user/agency.service
# Edit the file to set your paths

systemctl --user daemon-reload
systemctl --user enable --now agency.service
```

### Service Management

```bash
systemctl --user status agency.service       # Check status
systemctl --user restart agency.service      # Restart after code changes
journalctl --user -u agency.service -f       # Stream logs
```

## Running on macOS

Run directly with `python -m agency.app`. For persistence, create a launchd agent or use a process manager like `brew services`.

## Platform Support

Agency runs on any OS with Python 3.11+:

- **Linux** — full support including systemd dispatch timers
- **macOS** — full support including launchd dispatch timers
- **Windows** — app runs, dispatch timers require manual Task Scheduler setup

## Notes

- Agency assumes local/trusted access. There is no built-in authentication. Use a reverse proxy (Traefik, nginx, Caddy) if you need auth.
- Use a **user-level** systemd service on Linux, not system-level. System services cannot access user home directories on immutable OSes like Fedora Kinoite.
- The Python venv should be at `.venv/` in the project directory. The service file should reference `.venv/bin/python -m agency.app`.
