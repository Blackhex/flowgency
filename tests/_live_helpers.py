from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path


class _RegionExtractor(HTMLParser):
    """Inner HTML of each [data-live-region] root, sliced from the source markup."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self._html = html
        self._line_starts = [0] + [match.end() for match in re.finditer("\n", html)]
        self._open: tuple[str, str, int, int] | None = None
        self.regions: dict[str, str] = {}

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if self._open is None:
            key = dict(attrs).get("data-live-region")
            if key:
                start = self._offset() + len(self.get_starttag_text())
                self._open = (key, tag, 1, start)
        elif tag == self._open[1]:
            key, name, depth, start = self._open
            self._open = (key, name, depth + 1, start)

    def handle_endtag(self, tag):
        if self._open is None or tag != self._open[1]:
            return
        key, name, depth, start = self._open
        if depth == 1:
            self.regions[key] = self._html[start : self._offset()]
            self._open = None
        else:
            self._open = (key, name, depth - 1, start)


def page_regions(html: str) -> dict[str, str]:
    extractor = _RegionExtractor(html)
    extractor.feed(html)
    return extractor.regions


class _ElementExtractor(HTMLParser):
    """Outer HTML of the first `tag` whose attrs satisfy `predicate`, by matching-tag depth."""

    def __init__(self, html: str, tag: str, predicate) -> None:
        super().__init__(convert_charrefs=False)
        self._html = html
        self._line_starts = [0] + [match.end() for match in re.finditer("\n", html)]
        self._tag = tag
        self._predicate = predicate
        self._open: tuple[int, int] | None = None  # (depth, start)
        self.span: tuple[int, int] | None = None

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if self.span is not None or tag != self._tag:
            return
        if self._open is None:
            if self._predicate(dict(attrs)):
                self._open = (1, self._offset())
        else:
            depth, start = self._open
            self._open = (depth + 1, start)

    def handle_endtag(self, tag):
        if self._open is None or tag != self._tag:
            return
        depth, start = self._open
        if depth == 1:
            self.span = (start, self._offset() + len(f"</{tag}>"))
            self._open = None
        else:
            self._open = (depth - 1, start)


def find_element(html: str, tag: str, predicate) -> str | None:
    """Outer HTML of the first `tag` element whose attributes satisfy `predicate`.

    Identity-based alternative to substring/position slicing: matches by real
    attributes (e.g. data-live-key) and tracks nested same-tag depth to find the
    correct closing tag, rather than assuming fixed class ordering or offsets.
    """
    extractor = _ElementExtractor(html, tag, predicate)
    extractor.feed(html)
    if extractor.span is None:
        return None
    start, end = extractor.span
    return html[start:end]


def squash(markup: str) -> str:
    return re.sub(r">\s+<", "><", re.sub(r"\s+", " ", markup)).strip()


def live_regions(response) -> dict[str, str]:
    return {region["key"]: region["html"] for region in response.json()["regions"]}


def registration(html: str) -> dict:
    match = re.search(r'<script type="application/json" id="live-initial">(.*?)</script>', html, re.DOTALL)
    assert match is not None, "page does not embed a live registration"
    return json.loads(match.group(1))


def assert_snapshot_matches_page(page_html: str, response, *, skip: frozenset[str] = frozenset()) -> None:
    initial = {key: squash(html) for key, html in page_regions(page_html).items() if key not in skip}
    live = {key: squash(html) for key, html in live_regions(response).items() if key not in skip}
    assert initial == live


def filesystem_tree(*roots: Path) -> list[str]:
    entries: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            entries.append(f"{path}:{path.stat().st_size if path.is_file() else 'dir'}")
    return entries
