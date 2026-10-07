from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jinja2 import DictLoader, Environment
from starlette.templating import Jinja2Templates

from flowgency.web.live import (
    SHARED_NAVIGATION_TEMPLATE,
    LiveBinding,
    LivePagePolicy,
    LiveSnapshot,
    LiveSnapshotError,
    live_etag,
    live_registration,
    render_live_snapshot,
    respond_live_or_html,
    shared_region_macros,
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
        "meta", "math", "animate", "animateTransform", "animateMotion", "set",
        "foreignObject", "use", "image",
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


def test_inert_svg_icon_is_accepted():
    snapshot = _render_fragment(
        '<svg class="nav-icon" viewBox="0 0 24 24"><path stroke-width="2" d="M4 6h16"/></svg>'
    )

    assert "<path" in snapshot.regions[0].html


@pytest.mark.parametrize(
    "markup",
    [
        "<svg><script>x</script></svg>",
        '<svg onload="x()"></svg>',
        '<svg><a xlink:href="javascript:x()"><path d="M0 0"/></a></svg>',
        '<svg><use href="#a"/></svg>',
        "<svg><foreignObject><p>x</p></foreignObject></svg>",
        '<svg><path d="M0 0"><animate attributeName="d"/></path></svg>',
    ],
)
def test_active_svg_content_is_rejected(markup: str):
    with pytest.raises(LiveSnapshotError):
        _render_fragment(markup)


def _render_fragment(markup: str) -> LiveSnapshot:
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}" + markup + "{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    return render_live_snapshot(templates, {}, policy)


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


# ── Shared navigation regions ───────────────────────────────────────────────


def _shell_policy(**overrides: Any) -> LivePagePolicy:
    values: dict[str, Any] = dict(
        template_name="page.html",
        binding=LiveBinding(page="page", team="acme"),
        structure="page-shell:1",
        region_macros={},
        snapshot_url="/acme/page?__live=1",
    )
    values.update(overrides)
    return LivePagePolicy(**values)


def test_shared_region_macros_adds_team_navigation_regions():
    merged = shared_region_macros({"team": "acme"}, _shell_policy(region_macros={"rows": "live_rows"}))

    assert list(merged) == [
        "navigation-teams",
        "navigation-primary",
        "navigation-workflows",
        "navigation-workspace",
        "rows",
    ]
    assert merged["navigation-primary"] == f"{SHARED_NAVIGATION_TEMPLATE}#live_navigation_primary"
    assert merged["rows"] == "live_rows"


def test_shared_region_macros_for_admin_pages_only_keep_the_team_switcher():
    merged = shared_region_macros({"admin_active": True}, _shell_policy())

    assert list(merged) == ["navigation-teams"]


def test_shared_region_macros_rejects_a_page_region_that_shadows_navigation():
    with pytest.raises(LiveSnapshotError):
        shared_region_macros({}, _shell_policy(region_macros={"navigation-primary": "live_rows"}))


def test_qualified_macro_reference_renders_without_compiling_the_page_template():
    templates = Environment(
        loader=DictLoader({"shell.html": "{% macro live_nav() %}<a>{{ name }}</a>{% endmacro %}"}),
        autoescape=True,
    )
    policy = _shell_policy(template_name="missing-page.html", region_macros={"nav": "shell.html#live_nav"})

    snapshot = render_live_snapshot(templates, {"name": "Acme"}, policy)

    assert [(region.key, region.html) for region in snapshot.regions] == [("nav", "<a>Acme</a>")]


def test_qualified_and_page_macros_share_one_context():
    templates = Environment(
        loader=DictLoader(
            {
                "shell.html": "{% macro live_nav() %}<a>{{ name }}</a>{% endmacro %}",
                "page.html": "{% macro live_rows() %}<p>{{ name }}</p>{% endmacro %}",
            }
        ),
        autoescape=True,
    )
    policy = _shell_policy(region_macros={"nav": "shell.html#live_nav", "rows": "live_rows"})

    snapshot = render_live_snapshot(templates, {"name": "Acme"}, policy)

    assert [region.html for region in snapshot.regions] == ["<a>Acme</a>", "<p>Acme</p>"]


def test_qualified_reference_to_a_missing_macro_is_rejected():
    templates = Environment(loader=DictLoader({"shell.html": ""}), autoescape=True)

    with pytest.raises(LiveSnapshotError):
        render_live_snapshot(templates, {}, _shell_policy(region_macros={"nav": "shell.html#live_nav"}))


def test_live_registration_carries_only_non_secret_registration_data():
    registration = live_registration(_shell_policy(binding=LiveBinding(page="page", team="acme", query={"tab": "a"}),
                                                   snapshot_url="/acme/page?tab=a&__live=1"))

    assert registration == {
        "format": 1,
        "binding": {"page": "page", "team": "acme", "entity": None, "tab": None, "query": {"tab": "a"}},
        "structure": "page-shell:1",
        "url": "/acme/page?tab=a&__live=1",
    }


def test_html_render_receives_the_live_registration():
    env = Environment(
        loader=DictLoader({"sample.html": "{{ live_registration.url }}|{{ live_registration.structure }}"}),
        autoescape=True,
    )
    app = FastAPI()
    templates = Jinja2Templates(env=env)
    policy = _make_policy_for("sample.html")

    @app.get("/sample")
    async def sample(request: Request) -> Any:
        return respond_live_or_html(request, templates, {"request": request}, policy)

    assert TestClient(app).get("/sample").text == "/sample?__live=1|sample:1"


def _make_policy_for(template_name: str) -> LivePagePolicy:
    return LivePagePolicy(
        template_name=template_name,
        binding=LiveBinding(page="sample"),
        structure="sample:1",
        region_macros={},
        snapshot_url="/sample?__live=1",
    )


def _shell_context(**overrides: Any) -> dict[str, Any]:
    context: dict[str, Any] = {
        "team": "acme",
        "teams": {"acme": "Acme", "beta": "Beta <b>"},
        "active": "agents",
        "active_workflow_id": None,
        "nav_open_observations": 0,
        "nav_actionable": 0,
        "nav_agent_count": 3,
        "workflow_nav_available": True,
        "workflow_nav": [
            {"id": "delivery", "name": "Delivery", "count": 4, "status": "count"},
            {"id": "research", "name": "Research", "count": None, "status": "unavailable"},
        ],
        "workspaces_available": True,
        "workspaces": [{"name": "Grid"}],
    }
    context.update(overrides)
    return context


def _shell_snapshot(context: dict[str, Any]) -> LiveSnapshot:
    from flowgency.app import templates as app_templates

    policy = _shell_policy(template_name="agents.html")
    policy = replace(policy, region_macros=shared_region_macros(context, policy))
    return render_live_snapshot(app_templates, context, policy)


def _region_html(snapshot: LiveSnapshot, key: str) -> str:
    return next(region.html for region in snapshot.regions if region.key == key)


def test_shared_navigation_snapshot_keys_items_by_identity():
    snapshot = _shell_snapshot(_shell_context())

    assert [region.key for region in snapshot.regions] == [
        "navigation-teams",
        "navigation-primary",
        "navigation-workflows",
        "navigation-workspace",
    ]
    teams = _region_html(snapshot, "navigation-teams")
    assert 'data-live-key="team:acme"' in teams and "selected" in teams
    assert 'data-live-key="team:beta"' in teams
    assert "Beta &lt;b&gt;" in teams
    workflows = _region_html(snapshot, "navigation-workflows")
    assert 'data-live-key="workflow:delivery"' in workflows
    assert 'data-live-key="workflow:research"' in workflows
    assert 'data-live-key="nav:workspaces"' in _region_html(snapshot, "navigation-workspace")


def test_shared_navigation_snapshot_never_reports_an_unavailable_workflow_as_zero():
    workflows = _region_html(_shell_snapshot(_shell_context()), "navigation-workflows")

    research = workflows.split('data-live-key="workflow:research"')[1].split("</a>")[0]
    assert 'data-workflow-state="unavailable"' in research
    assert "Unavailable" in research
    assert 'data-workflow-state="count"' not in research
    assert ">0<" not in research


def test_shared_navigation_snapshot_marks_the_active_destination_only():
    context = _shell_context(active="workflow-board", active_workflow_id="delivery")
    snapshot = _shell_snapshot(context)

    workflows = _region_html(snapshot, "navigation-workflows")
    delivery = workflows.split('data-live-key="workflow:delivery"')[1].split(">")[0]
    research = workflows.split('data-live-key="workflow:research"')[1].split(">")[0]
    assert "active" in delivery and "active" not in research
    assert "nav-item active" not in _region_html(snapshot, "navigation-primary")


def test_shared_navigation_snapshot_hides_absent_sections_and_changes_with_membership():
    base = _shell_snapshot(_shell_context())
    bare = _shell_snapshot(
        _shell_context(workflow_nav_available=False, workflow_nav=[], workspaces_available=False, workspaces=[])
    )

    assert _region_html(bare, "navigation-workflows").strip() == ""
    assert 'data-live-key="nav:workspaces"' not in _region_html(bare, "navigation-workspace")
    assert live_etag(base) != live_etag(bare)
