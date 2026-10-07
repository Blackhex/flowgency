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


def test_duplicate_region_ids_rejected():
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>rows</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1",
        region_macros=[("rows", "live_rows"), ("rows", "live_rows_again")],
        snapshot_url="/sample?__live=1",
    )
    with pytest.raises(LiveSnapshotError, match="duplicate region id"):
        render_live_snapshot(templates, {}, policy)


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
    "tag", ["script", "iframe", "object", "embed", "base", "link", "style"]
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
