from __future__ import annotations

import pytest

from tests._live_helpers import live_regions, page_regions, squash

NAVIGATION_KEYS = ("navigation-teams", "navigation-primary", "navigation-workflows", "navigation-workspace")

# Every opted-in team page family the workflow fixture can serve, with its active destination.
PAGES = [
    ("/newsletter/", "nav:inbox"),
    ("/newsletter/agents", "nav:agents"),
    ("/newsletter/agents/builder/profile", "nav:agents"),
    ("/newsletter/agents/builder/memory", "nav:agents"),
    ("/newsletter/jobs", "nav:jobs"),
    ("/newsletter/logs", "nav:logs"),
    ("/newsletter/workspaces", "nav:workspaces"),
]


def _active_keys(regions: dict[str, str]) -> list[str]:
    import re

    keys: list[str] = []
    for html in regions.values():
        for match in re.finditer(r'<a [^>]*data-live-key="([^"]+)"[^>]*class="[^"]*\bactive\b[^"]*"', html):
            keys.append(match.group(1))
        for match in re.finditer(r'<a [^>]*class="[^"]*\bactive\b[^"]*"[^>]*data-live-key="([^"]+)"', html):
            keys.append(match.group(1))
    return keys


@pytest.mark.parametrize(("path", "active_key"), PAGES)
def test_shared_navigation_regions_match_between_the_page_and_its_snapshot(workflow_web_env, path, active_key):
    client = workflow_web_env.client

    page = client.get(path)
    snapshot = client.get(f"{path}?__live=1")

    assert page.status_code == 200 and snapshot.status_code == 200
    initial = {key: squash(html) for key, html in page_regions(page.text).items() if key in NAVIGATION_KEYS}
    live = {key: squash(html) for key, html in live_regions(snapshot).items() if key in NAVIGATION_KEYS}
    assert list(initial) == list(NAVIGATION_KEYS)
    assert initial == live
    assert _active_keys(initial) == [active_key]
    assert _active_keys(live) == [active_key]
