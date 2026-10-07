from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jinja2 import DictLoader, Environment
from starlette.templating import Jinja2Templates

from flowgency.web.live import (
    LiveBinding,
    LivePagePolicy,
    LiveSnapshot,
    LiveSnapshotError,
    live_etag,
    render_live_snapshot,
    respond_live_or_html,
)


# ── render_live_snapshot / live_etag ────────────────────────────────────────


def test_live_snapshot_escapes_text_and_changes_etag():
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>{{ name }}</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    first = render_live_snapshot(templates, {"name": "<script>bad()</script>"}, policy)
    assert "&lt;script&gt;" in first.regions[0].html
    second = render_live_snapshot(templates, {"name": "Changed"}, policy)
    assert live_etag(first) != live_etag(second)


def test_render_live_snapshot_shape_matches_policy():
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>{{ name }}</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample", team="acme"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    snapshot = render_live_snapshot(templates, {"name": "World"}, policy)
    assert isinstance(snapshot, LiveSnapshot)
    assert snapshot.format == 1
    assert snapshot.binding.page == "sample"
    assert snapshot.binding.team == "acme"
    assert snapshot.structure == "sample:1"
    assert len(snapshot.regions) == 1
    assert snapshot.regions[0].key == "rows"
    assert snapshot.regions[0].html == "<p>World</p>"


def test_render_live_snapshot_does_not_execute_extends_layout_or_body():
    calls: dict[str, int] = {}

    def probe(label: str) -> str:
        calls[label] = calls.get(label, 0) + 1
        return ""

    templates = Environment(
        loader=DictLoader(
            {
                "base.html": (
                    "{{ probe('base_top') }}"
                    "<html><body>"
                    "{% block content %}{{ probe('base_block_default') }}{% endblock %}"
                    "</body></html>"
                ),
                "sample.html": (
                    "{% extends 'base.html' %}"
                    "{{ probe('child_top') }}"
                    "{% macro live_rows() %}<p>{{ name }}</p>{% endmacro %}"
                    "{% block content %}<div>{{ probe('child_block') }}</div>{% endblock %}"
                ),
            }
        ),
        autoescape=True,
    )
    templates.globals["probe"] = probe

    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )

    snapshot = render_live_snapshot(templates, {"name": "<b>World</b>"}, policy)

    assert snapshot.regions[0].html == "<p>&lt;b&gt;World&lt;/b&gt;</p>"
    assert calls == {}


def test_render_live_snapshot_extends_with_top_level_import_still_resolves():
    # Modeled on workflow_board.html / ticket_detail.html / _ticket_inspector.html,
    # which all combine `{% extends %}` with a top-level
    # `{% import ... with context %}` whose macros the page's own macros call.
    calls: dict[str, int] = {}

    def probe(label: str) -> str:
        calls[label] = calls.get(label, 0) + 1
        return ""

    templates = Environment(
        loader=DictLoader(
            {
                "base.html": (
                    "{{ probe('base_top') }}"
                    "{% block content %}{{ probe('base_block_default') }}{% endblock %}"
                ),
                "_presentation.html": (
                    "{% macro greeting(value) -%}Hello {{ value }}{%- endmacro %}"
                ),
                "sample.html": (
                    "{% extends 'base.html' %}"
                    '{% import "_presentation.html" as p with context %}'
                    "{% macro live_rows() %}<p>{{ p.greeting(name) }}</p>{% endmacro %}"
                    "{% block content %}<div>{{ probe('child_block') }}</div>{% endblock %}"
                ),
            }
        ),
        autoescape=True,
    )
    templates.globals["probe"] = probe

    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )

    snapshot = render_live_snapshot(templates, {"name": "World"}, policy)

    assert snapshot.regions[0].html == "<p>Hello World</p>"
    assert calls == {}


def test_render_live_snapshot_extends_with_top_level_from_import_still_resolves():
    templates = Environment(
        loader=DictLoader(
            {
                "base.html": "{% block content %}{% endblock %}",
                "_presentation.html": (
                    "{% macro greeting(value) -%}Hi {{ value }}{%- endmacro %}"
                ),
                "sample.html": (
                    "{% extends 'base.html' %}"
                    '{% from "_presentation.html" import greeting %}'
                    "{% macro live_rows() %}<p>{{ greeting(name) }}</p>{% endmacro %}"
                    "{% block content %}{% endblock %}"
                ),
            }
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )

    snapshot = render_live_snapshot(templates, {"name": "World"}, policy)

    assert snapshot.regions[0].html == "<p>Hi World</p>"


def test_real_workflow_templates_macro_extraction_does_not_crash():
    # Quick probe against the real flowgency Jinja environment: workflow_board.html
    # and ticket_detail.html both extend a parent and import
    # "_ticket_presentation.html" with context at the top level. Extraction
    # (building the macro-only render context) must not raise.
    from flowgency.app import templates as app_templates
    from flowgency.web.live import _macro_only_context

    env = app_templates.env
    for template_name in ("workflow_board.html", "ticket_detail.html"):
        render_context = _macro_only_context(env, template_name, {})
        assert "presentation" in render_context.vars


def test_live_etag_is_stable_for_equivalent_snapshots():
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>{{ name }}</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    first = render_live_snapshot(templates, {"name": "World"}, policy)
    second = render_live_snapshot(templates, {"name": "World"}, policy)
    assert live_etag(first) == live_etag(second)
    assert live_etag(first).startswith('"') and live_etag(first).endswith('"')


# ── Validation failures: a clear ValueError subclass ────────────────────────


def test_duplicate_region_macro_name_rejected():
    # A dict can't have duplicate KEYS, but two different keys can legitimately
    # resolve to the same macro name -- that's the real collision to catch.
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>rows</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1",
        region_macros={"rows": "live_rows", "rows_again": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="duplicate region macro"):
        render_live_snapshot(templates, {}, policy)


def test_duplicate_data_live_key_across_regions_rejected():
    templates = Environment(
        loader=DictLoader(
            {
                "sample.html": (
                    '{% macro live_rows() %}<div data-live-key="shared">rows</div>{% endmacro %}'
                    '{% macro live_cards() %}<div data-live-key="shared">cards</div>{% endmacro %}'
                )
            }
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1",
        region_macros={"rows": "live_rows", "cards": "live_cards"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="duplicate data-live-key"):
        render_live_snapshot(templates, {}, policy)


def test_duplicate_root_id_within_region_rejected():
    templates = Environment(
        loader=DictLoader(
            {
                "sample.html": (
                    '{% macro live_rows() %}<div id="row-1">a</div><div id="row-1">b</div>{% endmacro %}'
                )
            }
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="duplicate root id"):
        render_live_snapshot(templates, {}, policy)


def test_valid_multi_region_policy_renders_all_regions():
    templates = Environment(
        loader=DictLoader(
            {
                "sample.html": (
                    '{% macro live_rows() %}<div data-live-key="rows-1">rows</div>{% endmacro %}'
                    '{% macro live_cards() %}<div data-live-key="cards-1">cards</div>{% endmacro %}'
                )
            }
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1",
        region_macros={"rows": "live_rows", "cards": "live_cards"},
        snapshot_url="/sample?__live=1",
    )
    snapshot = render_live_snapshot(templates, {}, policy)
    assert [region.key for region in snapshot.regions] == ["rows", "cards"]
    assert snapshot.regions[0].html == '<div data-live-key="rows-1">rows</div>'
    assert snapshot.regions[1].html == '<div data-live-key="cards-1">cards</div>'


def test_unsupported_structure_format_rejected():
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>rows</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="not a valid structure!",
        region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="structure"):
        render_live_snapshot(templates, {}, policy)


def test_query_binding_mismatch_rejected():
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>rows</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html",
        binding=LiveBinding(page="sample", query={"team": "acme"}),
        structure="sample:1",
        region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="query binding mismatch"):
        render_live_snapshot(templates, {}, policy)


@pytest.mark.parametrize(
    "macro_body",
    [
        '<button onclick="doStuff()">Click</button>',
        '<div onmouseover="doStuff()">Hover</div>',
    ],
)
def test_event_handler_attribute_rejected(macro_body: str):
    templates = Environment(
        loader=DictLoader({"sample.html": f"{{% macro live_rows() %}}{macro_body}{{% endmacro %}}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="event-handler"):
        render_live_snapshot(templates, {}, policy)


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "  JavaScript:alert(1)",
    ],
)
def test_executable_url_rejected(href: str):
    templates = Environment(
        loader=DictLoader({"sample.html": f'{{% macro live_rows() %}}<a href="{href}">x</a>{{% endmacro %}}'}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="executable URL"):
        render_live_snapshot(templates, {}, policy)


@pytest.mark.parametrize(
    "tag",
    [
        "script", "iframe", "object", "embed", "base", "link", "style",
        "meta", "svg", "math", "animate", "set", "foreignObject",
    ],
)
def test_unsafe_fragment_tag_rejected(tag: str):
    templates = Environment(
        loader=DictLoader({"sample.html": f"{{% macro live_rows() %}}<{tag}>x</{tag}>{{% endmacro %}}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="unsafe fragment tag"):
        render_live_snapshot(templates, {}, policy)


def test_srcdoc_attribute_rejected():
    templates = Environment(
        loader=DictLoader({"sample.html": '{% macro live_rows() %}<div srcdoc="x">y</div>{% endmacro %}'}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="srcdoc"):
        render_live_snapshot(templates, {}, policy)


def test_autofocus_attribute_rejected():
    templates = Environment(
        loader=DictLoader({"sample.html": '{% macro live_rows() %}<input autofocus>{% endmacro %}'}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="autofocus"):
        render_live_snapshot(templates, {}, policy)


def test_meta_refresh_rejected():
    templates = Environment(
        loader=DictLoader(
            {
                "sample.html": (
                    '{% macro live_rows() %}<meta http-equiv="refresh" '
                    'content="0;url=javascript:alert(1)">{% endmacro %}'
                )
            }
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="unsafe fragment tag"):
        render_live_snapshot(templates, {}, policy)


@pytest.mark.parametrize(
    "markup",
    [
        '<a xlink:href="javascript:alert(1)">x</a>',
        '<a ping="javascript:alert(1)">x</a>',
        '<a background="javascript:alert(1)">x</a>',
        '<a cite="javascript:alert(1)">x</a>',
        '<a manifest="javascript:alert(1)">x</a>',
        '<img srcset="javascript:alert(1)">',
        '<img srcset="good.png 1x, javascript:alert(1) 2x">',
    ],
)
def test_additional_url_attribute_vectors_rejected(markup: str):
    templates = Environment(
        loader=DictLoader({"sample.html": f"{{% macro live_rows() %}}{markup}{{% endmacro %}}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="executable URL"):
        render_live_snapshot(templates, {}, policy)


def test_executable_url_with_leading_control_character_rejected():
    templates = Environment(
        loader=DictLoader(
            {"sample.html": '{% macro live_rows() %}<a href="\x01javascript:alert(1)">x</a>{% endmacro %}'}
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="executable URL"):
        render_live_snapshot(templates, {}, policy)


def test_uppercase_tag_and_attribute_rejected():
    templates = Environment(
        loader=DictLoader(
            {"sample.html": '{% macro live_rows() %}<A HREF="JAVASCRIPT:alert(1)">x</A>{% endmacro %}'}
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="executable URL"):
        render_live_snapshot(templates, {}, policy)


def test_unquoted_executable_url_attribute_rejected():
    templates = Environment(
        loader=DictLoader(
            {"sample.html": "{% macro live_rows() %}<a href=javascript:alert(1)>x</a>{% endmacro %}"}
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="executable URL"):
        render_live_snapshot(templates, {}, policy)


def test_entity_obfuscated_scheme_rejected():
    templates = Environment(
        loader=DictLoader(
            {"sample.html": '{% macro live_rows() %}<a href="&#106;avascript:alert(1)">x</a>{% endmacro %}'}
        ),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="executable URL"):
        render_live_snapshot(templates, {}, policy)


# ── respond_live_or_html: HTML / JSON / conditional / failure replies ───────


_PAGE_TEMPLATE = (
    '{% macro live_rows() %}<div data-live-region="rows"><p>{{ name }}</p></div>{% endmacro %}'
    "<html><body>{{ live_rows() }}</body></html>"
)


def _make_templates() -> Jinja2Templates:
    env = Environment(loader=DictLoader({"sample.html": _PAGE_TEMPLATE}), autoescape=True)
    return Jinja2Templates(env=env)


def _make_policy() -> LivePagePolicy:
    return LivePagePolicy(
        template_name="sample.html",
        binding=LiveBinding(page="sample"),
        structure="sample:1",
        region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )


def _make_app(*, fail: bool = False) -> FastAPI:
    app = FastAPI()
    templates = _make_templates()
    policy = _make_policy()

    @app.get("/sample")
    async def sample(request: Request) -> Any:
        name = request.query_params.get("name", "World")
        context = {"request": request, "name": name}
        status_code = 422 if fail else 200
        return respond_live_or_html(request, templates, context, policy, status_code=status_code)

    return app


def test_ordinary_get_returns_html():
    client = TestClient(_make_app())
    response = client.get("/sample")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<p>World</p>" in response.text


def test_explicit_live_get_returns_json_snapshot_with_cache_headers():
    client = TestClient(_make_app())
    response = client.get("/sample", params={"__live": "1"})
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["etag"]
    assert response.headers["vary"]
    body = response.json()
    assert body["format"] == 1
    assert body["binding"]["page"] == "sample"
    assert body["regions"][0]["key"] == "rows"
    assert body["regions"][0]["html"] == '<div data-live-region="rows"><p>World</p></div>'


def test_conditional_get_returns_304_with_same_etag():
    client = TestClient(_make_app())
    first = client.get("/sample", params={"__live": "1"})
    etag = first.headers["etag"]
    second = client.get("/sample", params={"__live": "1"}, headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert second.headers["cache-control"] == "private, no-cache"
    assert second.content == b""


def test_conditional_get_with_stale_etag_returns_fresh_json():
    client = TestClient(_make_app())
    stale_etag = '"0000000000000000000000000000000000000000000000000000000000000000"'
    response = client.get("/sample", params={"__live": "1"}, headers={"If-None-Match": stale_etag})
    assert response.status_code == 200
    assert response.headers["etag"] != stale_etag


def test_failure_reply_html_is_not_cacheable():
    client = TestClient(_make_app(fail=True))
    response = client.get("/sample")
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_failure_reply_json_is_not_cacheable():
    client = TestClient(_make_app(fail=True))
    response = client.get("/sample", params={"__live": "1"})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["format"] == 1
