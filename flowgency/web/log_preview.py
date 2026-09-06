from dataclasses import dataclass
import json
from pathlib import Path

import markdown
import nh3

from flowgency.integrations.flowgency.copilot_output import assistant_message, event_objects


SOURCE_LIMIT = 4 * 1024 * 1024
DISPLAY_LIMIT = 64 * 1024
LOG_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em",
    "del", "a", "table", "thead", "tbody", "tr", "th", "td",
}


@dataclass(frozen=True)
class LogPreview:
    text: str
    content_html: str | None
    truncated: bool


def _is_event_stream(raw: str) -> bool:
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except (ValueError, RecursionError):
            if not stripped.startswith(("{", "[")):
                return False
            continue
        if not isinstance(value, dict):
            continue
        kind = value.get("type")
        if not isinstance(kind, str):
            continue
        if kind == "result" or kind.startswith(("session.", "assistant.", "tool.", "user.", "system.", "model.")):
            return True
    return False


def read_log_preview(path: Path) -> LogPreview:
    with path.open("rb") as stream:
        source = stream.read(SOURCE_LIMIT + 1)
    truncated = len(source) > SOURCE_LIMIT
    raw = source[:SOURCE_LIMIT].decode("utf-8", errors="replace")
    render_markdown = path.suffix == ".out"
    is_event_stream = render_markdown and _is_event_stream(raw)
    if render_markdown and truncated and not is_event_stream and raw.lstrip().startswith(("{", "[")):
        render_markdown = False
    if is_event_stream:
        complete = raw
        if truncated and not raw.endswith("\n"):
            complete = raw.rpartition("\n")[0]
        messages = [message for event in event_objects(complete)
                    if (message := assistant_message(event)) is not None]
        if messages:
            raw = "\n".join(messages)
        else:
            render_markdown = False
    display = raw.encode("utf-8")
    truncated = truncated or len(display) > DISPLAY_LIMIT
    text = display[:DISPLAY_LIMIT].decode("utf-8", errors="ignore")
    content_html = None
    if render_markdown:
        converted = markdown.Markdown(extensions=["tables", "fenced_code", "nl2br"]).convert(text)
        content_html = nh3.clean(
            converted,
            tags=LOG_TAGS,
            attributes={"a": {"href", "title"}, "code": {"class"}},
            clean_content_tags={"script", "style", "iframe", "object", "embed", "form"},
            url_schemes={"http", "https", "mailto"},
            link_rel="noopener noreferrer",
        )
    return LogPreview(text=text, content_html=content_html, truncated=truncated)

