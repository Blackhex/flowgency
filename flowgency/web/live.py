from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal, Mapping
from urllib.parse import parse_qsl, urlsplit
from weakref import WeakKeyDictionary

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from jinja2 import nodes
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LiveSnapshotError",
    "LiveBinding",
    "LiveRegion",
    "LiveSnapshot",
    "LivePagePolicy",
    "render_live_snapshot",
    "live_etag",
    "live_registration",
    "respond_live_or_html",
    "shared_region_macros",
    "SHARED_NAVIGATION_TEMPLATE",
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


# A region macro is normally a top-level macro of the policy's own template. A
# "<template>#<macro>" reference names a macro of another template instead, so
# shared regions (base.html navigation) need no import in every page template.
_MACRO_REFERENCE_SEPARATOR = "#"

SHARED_NAVIGATION_TEMPLATE = "_live_shell.html"
_SHARED_NAVIGATION_REGIONS: Mapping[str, str] = {
    "navigation-teams": "live_navigation_teams",
    "navigation-primary": "live_navigation_primary",
    "navigation-workflows": "live_navigation_workflows",
    "navigation-workspace": "live_navigation_workspace",
}
# Admin pages render the static admin menu; only the team switcher is data-driven.
_SHARED_ADMIN_REGIONS = frozenset({"navigation-teams"})

# A structure identifier is an explicit "<name>:<version>" compatibility token,
# never an mtime, content hash or other control-plane schema value.
_STRUCTURE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*:[0-9]+$")

# Inline <svg> icons are inert; the active svg elements below stay blocked.
_UNSAFE_TAGS = frozenset({
    "script", "iframe", "object", "embed", "base", "link", "style",
    "meta", "math", "animate", "animatetransform", "animatemotion", "set",
    "foreignobject", "use", "image",
})
_UNSAFE_ATTRS = frozenset({"srcdoc", "autofocus"})
_URL_ATTRS = frozenset({
    "href", "src", "action", "formaction", "poster", "data",
    "xlink:href", "srcset", "ping", "background", "cite", "manifest",
})
_UNSAFE_URL_SCHEMES = ("javascript:", "data:text/html", "vbscript:")
# Browsers strip leading/embedded C0 controls before parsing a URL scheme.
_CONTROL_OR_WHITESPACE = re.compile(r"[\x00-\x20]")


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
                for candidate in _url_candidates(name_lower, value):
                    normalized = _CONTROL_OR_WHITESPACE.sub("", candidate).lower()
                    if normalized.startswith(_UNSAFE_URL_SCHEMES):
                        self.violations.append(f"executable URL in {name_lower!r}")
                        break


def _url_candidates(attr_name: str, value: str) -> list[str]:
    """srcset packs multiple comma-separated '<url> <descriptor>?' candidates."""
    if attr_name != "srcset":
        return [value]
    candidates = []
    for part in value.split(","):
        token = part.strip()
        if token:
            candidates.append(token.split()[0] if token.split() else token)
    return candidates


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


def _region_items(region_macros: Mapping[str, str]) -> list[tuple[str, str]]:
    # A real Mapping can't hold duplicate keys; the only reachable collision
    # is two region keys resolving to the same macro name.
    items = list(region_macros.items())
    seen_macros: set[str] = set()
    for _key, macro_name in items:
        if macro_name in seen_macros:
            raise LiveSnapshotError(
                f"duplicate region macro: {macro_name!r} is bound to more than one region"
            )
        seen_macros.add(macro_name)
    return items


class _KeyCollector(HTMLParser):
    """Collects `id`/`data-live-key` values so keyed-reconciliation collisions can be found."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.live_keys: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(attrs)

    def _collect(self, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if not value:
                continue
            name_lower = (name or "").lower()
            if name_lower == "id":
                self.ids.append(value)
            elif name_lower == "data-live-key":
                self.live_keys.append(value)


def _check_region_key_collisions(regions: list[LiveRegion]) -> None:
    seen_ids: dict[str, str] = {}
    seen_live_keys: dict[str, str] = {}
    for region in regions:
        collector = _KeyCollector()
        collector.feed(region.html)
        collector.close()
        for value in collector.ids:
            if value in seen_ids:
                raise LiveSnapshotError(
                    f"duplicate root id {value!r} in region {region.key!r} "
                    f"(already present in region {seen_ids[value]!r})"
                )
            seen_ids[value] = region.key
        for value in collector.live_keys:
            if value in seen_live_keys:
                raise LiveSnapshotError(
                    f"duplicate data-live-key {value!r} in region {region.key!r} "
                    f"(already present in region {seen_live_keys[value]!r})"
                )
            seen_live_keys[value] = region.key


class _NonRenderingParent:
    """Stands in for an `{% extends %}` parent: contributes no blocks, renders nothing."""

    blocks: Mapping[str, Any] = {}

    def root_render_func(self, context: Any) -> Any:
        return iter(())


def _literal_template_names(expr: Any) -> frozenset[str] | None:
    """Resolve a compile-time-constant `{% extends %}` target to its literal
    name(s), or None if it is not fully known at compile time (e.g. a
    variable, or a `select_template`-style list containing one)."""
    if isinstance(expr, nodes.Const) and isinstance(expr.value, str):
        return frozenset({expr.value})
    if isinstance(expr, (nodes.Tuple, nodes.List)):
        names: set[str] = set()
        for item in expr.items:
            if not (isinstance(item, nodes.Const) and isinstance(item.value, str)):
                return None
            names.add(item.value)
        return frozenset(names)
    return None


def _extends_parent_names(env: Any, source: str, template_name: str) -> frozenset[str]:
    """Find the literal template name(s) targeted by this template's own
    top-level `{% extends %}`. Only these names may resolve to the
    non-rendering stub; `{% import %}`, `{% from ... import %}` and
    `{% include %}` of any other template must still resolve for real."""
    ast = env.parse(source, name=template_name)
    names: set[str] = set()
    for extends_node in ast.find_all(nodes.Extends):
        literal_names = _literal_template_names(extends_node.template)
        if literal_names is None:
            raise LiveSnapshotError(
                f"template {template_name!r} extends a non-literal template name; "
                "live snapshot macro extraction requires a literal {% extends %} target"
            )
        names.update(literal_names)
    return frozenset(names)


# Per real Jinja Environment, a compiled template whose extends resolution is
# stubbed out -- so top-level macros can be read without rendering any parent.
_macro_template_cache: "WeakKeyDictionary[Any, dict[str, Any]]" = WeakKeyDictionary()


def _macro_only_context(env: Any, template_name: str, context_vars: dict[str, Any]) -> Any:
    """Build a render context with top-level macros defined, without executing
    any `{% extends %}` parent's layout/body or the template's own non-macro
    output. Top-level `{% import %}`, `{% from ... import %}` and
    `{% include %}` of other templates still resolve through the real
    environment, so their macros remain usable."""
    per_env_cache = _macro_template_cache.setdefault(env, {})
    fresh_template = per_env_cache.get(template_name)
    if fresh_template is None:
        source, filename, _ = env.loader.get_source(env, template_name)
        parent_names = _extends_parent_names(env, source, template_name)
        overlay = env.overlay()
        real_get_template = overlay.get_template
        real_select_template = overlay.select_template
        real_get_or_select_template = overlay.get_or_select_template

        def _stub_get_template(name: str, parent: str | None = None, globals: Any = None) -> Any:
            if name in parent_names:
                return _NonRenderingParent()
            return real_get_template(name, parent, globals)

        def _stub_select_template(names: Any, parent: str | None = None, globals: Any = None) -> Any:
            if parent_names and parent_names.issubset(list(names)):
                return _NonRenderingParent()
            return real_select_template(names, parent, globals)

        def _stub_get_or_select_template(
            name_or_list: Any, parent: str | None = None, globals: Any = None
        ) -> Any:
            if isinstance(name_or_list, str):
                if name_or_list in parent_names:
                    return _NonRenderingParent()
            elif parent_names and parent_names.issubset(list(name_or_list)):
                return _NonRenderingParent()
            return real_get_or_select_template(name_or_list, parent, globals)

        overlay.get_template = _stub_get_template
        overlay.select_template = _stub_select_template
        overlay.get_or_select_template = _stub_get_or_select_template
        code = overlay.compile(source, name=template_name, filename=filename)
        fresh_template = overlay.template_class.from_code(overlay, code, overlay.globals, uptodate=None)
        per_env_cache[template_name] = fresh_template

    render_context = fresh_template.new_context(context_vars)
    for _ in fresh_template.root_render_func(render_context):
        pass
    return render_context


def render_live_snapshot(
    templates: Any, context: dict[str, Any], policy: LivePagePolicy
) -> LiveSnapshot:
    """Render only the policy's declared macros; never a full page or an internal request."""
    _validate_structure(policy.structure)
    _validate_query_binding(policy)
    items = _region_items(policy.region_macros)

    env = templates.env if hasattr(templates, "env") else templates
    macro_contexts: dict[str, Any] = {}

    regions: list[LiveRegion] = []
    for key, reference in items:
        template_name, _, macro_name = reference.rpartition(_MACRO_REFERENCE_SEPARATOR)
        template_name = template_name or policy.template_name
        # Compile each template once per snapshot, and only when a region needs it.
        if template_name not in macro_contexts:
            macro_contexts[template_name] = _macro_only_context(env, template_name, context)
        try:
            macro = macro_contexts[template_name].vars[macro_name]
        except KeyError:
            raise LiveSnapshotError(
                f"template {template_name!r} defines no top-level macro {macro_name!r}"
            ) from None
        html = str(macro())
        _validate_fragment_safety(html)
        regions.append(LiveRegion(key=key, html=html))

    _check_region_key_collisions(regions)

    return LiveSnapshot(binding=policy.binding, structure=policy.structure, regions=regions)


def shared_region_macros(context: Mapping[str, Any], policy: LivePagePolicy) -> Mapping[str, str]:
    """The policy's own region macros plus the shared navigation regions that
    ``base.html`` renders for this context, ready for ``replace(policy, region_macros=...)``."""
    admin = bool(context.get("admin_active"))
    merged: dict[str, str] = {}
    for key, macro_name in _SHARED_NAVIGATION_REGIONS.items():
        if admin and key not in _SHARED_ADMIN_REGIONS:
            continue
        merged[key] = f"{SHARED_NAVIGATION_TEMPLATE}{_MACRO_REFERENCE_SEPARATOR}{macro_name}"
    for key, macro_name in policy.region_macros.items():
        if key in merged:
            raise LiveSnapshotError(f"page region {key!r} collides with a shared navigation region")
        merged[key] = macro_name
    return merged


def live_registration(policy: LivePagePolicy) -> dict[str, Any]:
    """Non-secret registration data a page embeds as ``#live-initial`` (via ``tojson``)."""
    return {
        "format": 1,
        "binding": policy.binding.model_dump(mode="json"),
        "structure": policy.structure,
        "url": policy.snapshot_url,
    }


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
    html_context = {**context, "live_registration": live_registration(policy)}

    if status_code >= 400:
        headers = {"Cache-Control": "no-store"}
        if wants_live:
            snapshot = render_live_snapshot(templates, context, policy)
            return JSONResponse(
                snapshot.model_dump(mode="json"), status_code=status_code, headers=headers
            )
        return templates.TemplateResponse(
            request, policy.template_name, html_context, status_code=status_code, headers=headers
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

    return templates.TemplateResponse(request, policy.template_name, html_context, status_code=status_code)
