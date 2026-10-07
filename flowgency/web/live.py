from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal, Mapping
from urllib.parse import parse_qsl, urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LiveSnapshotError",
    "LiveBinding",
    "LiveRegion",
    "LiveSnapshot",
    "LivePagePolicy",
    "render_live_snapshot",
    "live_etag",
    "respond_live_or_html",
]


class LiveSnapshotError(ValueError):
    """A live snapshot policy or its rendered fragment is malformed or unsafe."""


class LiveBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: str
    team: str | None = None
    entity: str | None = None
    tab: str | None = None
    query: dict[str, str] = Field(default_factory=dict)


class LiveRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    html: str


class LiveSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal[1] = 1
    binding: LiveBinding
    structure: str
    revisions: dict[str, str] = Field(default_factory=dict)
    regions: list[LiveRegion]


@dataclass(frozen=True)
class LivePagePolicy:
    template_name: str
    binding: LiveBinding
    structure: str
    region_macros: Mapping[str, str]
    snapshot_url: str


# A structure identifier is an explicit "<name>:<version>" compatibility token,
# never an mtime, content hash or other control-plane schema value.
_STRUCTURE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*:[0-9]+$")

_UNSAFE_TAGS = frozenset({"script", "iframe", "object", "embed", "base", "link", "style"})
_UNSAFE_ATTRS = frozenset({"srcdoc", "autofocus"})
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "poster", "data"})
_UNSAFE_URL_SCHEMES = ("javascript:", "data:text/html", "vbscript:")


class _FragmentSafetyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def _check(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in _UNSAFE_TAGS:
            self.violations.append(f"unsafe fragment tag <{tag_lower}>")
        for name, value in attrs:
            name_lower = (name or "").lower()
            if name_lower in _UNSAFE_ATTRS:
                self.violations.append(f"unsafe fragment attribute {name_lower!r}")
            elif name_lower.startswith("on"):
                self.violations.append(f"event-handler attribute {name_lower!r}")
            elif name_lower in _URL_ATTRS and value:
                normalized = "".join(value.split()).lower()
                if normalized.startswith(_UNSAFE_URL_SCHEMES):
                    self.violations.append(f"executable URL in {name_lower!r}")


def _validate_fragment_safety(html_fragment: str) -> None:
    parser = _FragmentSafetyParser()
    parser.feed(html_fragment)
    parser.close()
    if parser.violations:
        raise LiveSnapshotError("; ".join(parser.violations))


def _validate_structure(structure: str) -> None:
    if not _STRUCTURE_PATTERN.match(structure):
        raise LiveSnapshotError(f"unsupported structure format: {structure!r}")


def _validate_query_binding(policy: LivePagePolicy) -> None:
    parsed = urlsplit(policy.snapshot_url)
    url_query = {
        key: value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "__live"
    }
    binding_query = dict(policy.binding.query)
    if url_query != binding_query:
        raise LiveSnapshotError(
            f"query binding mismatch: snapshot_url query {url_query!r} does not match "
            f"binding.query {binding_query!r}"
        )


def _region_items(region_macros: Mapping[str, str] | Any) -> list[tuple[str, str]]:
    items = (
        list(region_macros.items())
        if isinstance(region_macros, Mapping)
        else list(region_macros)
    )
    seen: set[str] = set()
    for key, _macro_name in items:
        if key in seen:
            raise LiveSnapshotError(f"duplicate region id: {key!r}")
        seen.add(key)
    return items


def render_live_snapshot(
    templates: Any, context: dict[str, Any], policy: LivePagePolicy
) -> LiveSnapshot:
    """Render only the policy's declared macros; never discover regions by parsing a full page."""
    _validate_structure(policy.structure)
    _validate_query_binding(policy)
    items = _region_items(policy.region_macros)

    env = templates.env if hasattr(templates, "env") else templates
    template = env.get_template(policy.template_name)
    module = template.make_module(vars=context)

    regions: list[LiveRegion] = []
    for key, macro_name in items:
        macro = getattr(module, macro_name)
        html = str(macro())
        _validate_fragment_safety(html)
        regions.append(LiveRegion(key=key, html=html))

    return LiveSnapshot(binding=policy.binding, structure=policy.structure, regions=regions)


def live_etag(snapshot: LiveSnapshot) -> str:
    serialized = json.dumps(
        snapshot.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return '"' + hashlib.sha256(serialized).hexdigest() + '"'


def respond_live_or_html(
    request: Request,
    templates: Any,
    context: dict[str, Any],
    policy: LivePagePolicy,
    *,
    status_code: int = 200,
) -> Response:
    wants_live = request.query_params.get("__live") == "1"

    if status_code >= 400:
        headers = {"Cache-Control": "no-store"}
        if wants_live:
            snapshot = render_live_snapshot(templates, context, policy)
            return JSONResponse(
                snapshot.model_dump(mode="json"), status_code=status_code, headers=headers
            )
        return templates.TemplateResponse(
            request, policy.template_name, context, status_code=status_code, headers=headers
        )

    if wants_live:
        snapshot = render_live_snapshot(templates, context, policy)
        etag = live_etag(snapshot)
        # Private: content may depend on the caller's session cookie, so it must
        # never be reused by a shared cache or across a different signed-in session.
        headers = {
            "Cache-Control": "private, no-cache",
            "ETag": etag,
            "Vary": "Cookie",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(snapshot.model_dump(mode="json"), status_code=status_code, headers=headers)

    return templates.TemplateResponse(request, policy.template_name, context, status_code=status_code)
