"""Closed completion contract for explicit setup-completion decisions.

Configuration readiness alone never proves setup is finished; these types
require an explicit, strictly-typed assertion that setup work and scheduler
disposition are both closed before a completion decision can be formed.
"""

from __future__ import annotations

import http.client
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

from flowgency.blueprints import BlueprintLibrary
from flowgency.configuration import ConfigStore, ValidationFailed
from flowgency.configuration.paths import validate_resolved_paths
from flowgency.integrations import REGISTRY
from flowgency.prompts import validate_prompt_catalogs
from flowgency.prompts.store import ReadOnlyPromptStore
from flowgency.web.dependencies import FlowgencyServices
from flowgency.web.validation import collect_validation_issues

SchedulerResult = Literal[
    "manual-only", "inactive", "declined", "confirmed", "failed", "unknown"
]

_LAUNCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEDULER_RESULTS_REQUIRING_ACKNOWLEDGEMENT = {"failed", "unknown"}


class SetupCompletionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launch_id: StrictStr
    revision: StrictStr
    scheduler_result: SchedulerResult
    all_questions_answered: StrictBool
    summary_delivered: StrictBool
    limitations_acknowledged: StrictBool = False

    @model_validator(mode="after")
    def validate_identifiers(self) -> "SetupCompletionCommand":
        if not _LAUNCH_ID_RE.fullmatch(self.launch_id):
            raise ValueError("launch_id must be a 32-character lowercase hex UUID value")
        if not _REVISION_RE.fullmatch(self.revision):
            raise ValueError("revision must be a 64-character lowercase SHA-256 hex value")
        return self

    @model_validator(mode="after")
    def require_finished_work(self) -> "SetupCompletionCommand":
        if self.all_questions_answered is not True or self.summary_delivered is not True:
            raise ValueError("Setup work is not complete")
        if (
            self.scheduler_result in _SCHEDULER_RESULTS_REQUIRING_ACKNOWLEDGEMENT
            and not self.limitations_acknowledged
        ):
            raise ValueError("Scheduler limitation requires acknowledgement")
        return self


@dataclass(frozen=True)
class SetupCompletionLaunch:
    launch_id: str
    origin: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class SetupCompletionDecision:
    launch_id: str | None
    phase: Literal["pending", "acknowledged", "complete", "attention", "cancelled"]
    redirect_allowed: bool
    message: str


@dataclass(frozen=True)
class SetupExitCapability:
    supported: bool
    cli_version: str
    reason: str


ExitBoundary = Literal["busy", "prompting", "safe", "unsupported"]


class SetupExitAdapter(Protocol):
    """Reports the interactive CLI's programmatic boundary; never inferred from screen text."""

    def capability(self) -> SetupExitCapability: ...

    def boundary(self) -> ExitBoundary: ...


# Measured by tools/probe_setup_exit.py: no verified idle boundary for the interactive session.
COPILOT_EXIT_CAPABILITY = SetupExitCapability(
    supported=False,
    cli_version="1.0.93-1",
    reason=(
        "No supported, measured completed/idle boundary exists for the interactive "
        "session; /exit alone is not evidence that automatic exit is safe."
    ),
)


class UnsupportedExitAdapter:
    def __init__(self, capability: SetupExitCapability = COPILOT_EXIT_CAPABILITY) -> None:
        self._capability = capability

    def capability(self) -> SetupExitCapability:
        return self._capability

    def boundary(self) -> ExitBoundary:
        return "unsupported"


def completion_environment(launch: SetupCompletionLaunch) -> dict[str, str]:
    return {
        "FLOWGENCY_SETUP_ORIGIN": launch.origin,
        "FLOWGENCY_SETUP_TOKEN": launch.token,
        "FLOWGENCY_SETUP_LAUNCH_ID": launch.launch_id,
    }


class SetupCompletionStale(Exception):
    """The configuration revision no longer matches the one the agent reported."""


class SetupCompletionUnavailable(Exception):
    """The configuration could not be shown ready; carries only a fixed code."""

    def __init__(self, code: Literal["not-ready", "unavailable"]) -> None:
        self.code = code
        super().__init__(code)


_UNREADABLE_CONFIG = (FileNotFoundError, ValidationFailed, yaml.YAMLError, TypeError, ValueError)


def _require_ready(config_store: ConfigStore, snapshot) -> None:
    config = snapshot.config
    if not config.teams or validate_resolved_paths(config):
        raise SetupCompletionUnavailable("not-ready")
    flowgency = config.flowgency
    library = BlueprintLibrary(Path(flowgency.agent_library))
    services = FlowgencyServices(
        config_path=snapshot.path,
        config_store=config_store,
        blueprint_library=library,
        compilation_cache=None,
        memory_store=None,
        prompt_store=None,
        job_store=None,
        instances=None,
        integrations=REGISTRY,
        prompt_issues=validate_prompt_catalogs(
            snapshot, library, ReadOnlyPromptStore(Path(flowgency.prompt_store))
        ),
    )
    if collect_validation_issues(services, snapshot):
        raise SetupCompletionUnavailable("not-ready")


def validate_current_completion(config_path: Path, expected_revision: str) -> str:
    """Re-check readiness without creating or repairing any source; return the revision."""
    store = ConfigStore(config_path)
    try:
        if not store.path.is_file():
            raise FileNotFoundError
        snapshot = store.load(create_lock_parent=False)
    except _UNREADABLE_CONFIG:
        raise SetupCompletionUnavailable("not-ready") from None
    except Exception:
        raise SetupCompletionUnavailable("unavailable") from None
    if snapshot.revision != expected_revision:
        raise SetupCompletionStale()
    try:
        _require_ready(store, snapshot)
        current = store.inspect(create_lock_parent=False)
    except SetupCompletionUnavailable:
        raise
    except (FileNotFoundError, ValidationFailed):
        raise SetupCompletionUnavailable("not-ready") from None
    except Exception:
        raise SetupCompletionUnavailable("unavailable") from None
    if not current.exists or current.revision != expected_revision:
        raise SetupCompletionStale()
    return current.revision


COMPLETION_TIMEOUT_SECONDS = 5.0
COMPLETION_RESPONSE_LIMIT = 16 * 1024
_COMPLETION_PATH = "/setup/session/completion"
_ORIGIN_VARIABLE = "FLOWGENCY_SETUP_ORIGIN"
_TOKEN_VARIABLE = "FLOWGENCY_SETUP_TOKEN"
_LAUNCH_VARIABLE = "FLOWGENCY_SETUP_LAUNCH_ID"
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,256}")
_SERVER_REFUSAL_CODES = frozenset(
    {
        "invalid-credentials",
        "forbidden",
        "payload-too-large",
        "invalid-completion",
        "stale",
        "not-ready",
        "unavailable",
    }
)
_CLIENT_ERROR_MESSAGES = {
    "missing-context": "Flowgency setup launch context is not available.",
    "invalid-context": "Flowgency setup launch context is malformed or does not match.",
    "invalid-origin": "The setup callback origin is not an accepted loopback address.",
    "unreachable": "The Flowgency setup callback could not be reached.",
    "timeout": "The Flowgency setup callback did not answer in time.",
    "redirect": "The Flowgency setup callback attempted a redirect, which is refused.",
    "response-too-large": "The Flowgency setup callback response was too large.",
    "invalid-response": "The Flowgency setup callback returned an unrecognised response.",
    "invalid-credentials": "The setup completion credentials were rejected.",
    "forbidden": "The setup callback is only available to the local setup process.",
    "payload-too-large": "The setup completion payload was too large.",
    "invalid-completion": "The setup completion report was rejected.",
    "stale": "The setup launch or saved configuration changed; report completion again.",
    "not-ready": "The saved configuration is not ready to be completed.",
    "unavailable": "Setup completion could not be verified right now.",
}


class SetupCompletionClientError(Exception):
    """A callback failure carrying only a fixed code and message, never a response body."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = _CLIENT_ERROR_MESSAGES[code]
        super().__init__(self.message)


def _pinned_origin(value: str) -> tuple[str, str, int | None]:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        loopback = host is not None and ip_address(host).is_loopback
    except ValueError:
        raise SetupCompletionClientError("invalid-origin") from None
    if (
        parts.scheme not in ("http", "https")
        or not loopback
        or parts.username is not None
        or parts.password is not None
        or parts.path
        or parts.query
        or parts.fragment
    ):
        raise SetupCompletionClientError("invalid-origin")
    return parts.scheme, host, port


def _read_bounded(response: http.client.HTTPResponse, deadline: float) -> bytes:
    declared = response.getheader("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > COMPLETION_RESPONSE_LIMIT:
        raise SetupCompletionClientError("response-too-large")
    chunks: list[bytes] = []
    received = 0
    while True:
        if time.monotonic() > deadline:
            raise SetupCompletionClientError("timeout")
        chunk = response.read(min(4096, COMPLETION_RESPONSE_LIMIT + 1 - received))
        if not chunk:
            return b"".join(chunks)
        received += len(chunk)
        if received > COMPLETION_RESPONSE_LIMIT:
            raise SetupCompletionClientError("response-too-large")
        chunks.append(chunk)


def submit_completion(
    command: SetupCompletionCommand, environment: Mapping[str, str]
) -> dict:
    """Send one completion report to the pinned loopback origin using the launch capability."""
    origin = environment.get(_ORIGIN_VARIABLE)
    token = environment.get(_TOKEN_VARIABLE)
    launch_id = environment.get(_LAUNCH_VARIABLE)
    if not origin or not token or not launch_id:
        raise SetupCompletionClientError("missing-context")
    if launch_id != command.launch_id or not _TOKEN_RE.fullmatch(token):
        raise SetupCompletionClientError("invalid-context")
    scheme, host, port = _pinned_origin(origin)

    # http.client never consults proxy variables and never follows redirects.
    connection_class = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_class(host, port, timeout=COMPLETION_TIMEOUT_SECONDS)
    deadline = time.monotonic() + COMPLETION_TIMEOUT_SECONDS
    try:
        connection.request(
            "POST",
            _COMPLETION_PATH,
            body=command.model_dump_json().encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        status = response.status
        if 300 <= status < 400:
            raise SetupCompletionClientError("redirect")
        raw = _read_bounded(response, deadline)
    except SetupCompletionClientError:
        raise
    except TimeoutError:
        raise SetupCompletionClientError("timeout") from None
    except (OSError, http.client.HTTPException):
        raise SetupCompletionClientError("unreachable") from None
    finally:
        connection.close()

    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        raise SetupCompletionClientError("invalid-response") from None
    if not isinstance(payload, dict):
        raise SetupCompletionClientError("invalid-response")
    if 200 <= status < 300:
        if payload.get("ok") is True and isinstance(payload.get("completion"), dict):
            return payload
        raise SetupCompletionClientError("invalid-response")
    code = payload.get("code")
    if payload.get("ok") is False and code in _SERVER_REFUSAL_CODES:
        raise SetupCompletionClientError(code)
    raise SetupCompletionClientError("invalid-response")
