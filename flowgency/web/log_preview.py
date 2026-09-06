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
    """Read a log file and produce a bounded preview with optional HTML rendering.
    
    Reads at most SOURCE_LIMIT + 1 bytes. For event streams with assistant.message
    events, renders Markdown to HTML and sanitizes. Otherwise returns plain text.
    Never writes the file.
    """
    # Read raw bytes with one extra byte to detect truncation
    with path.open("rb") as f:
        raw_bytes = f.read(SOURCE_LIMIT + 1)
    
    # Detect truncation
    was_truncated = len(raw_bytes) > SOURCE_LIMIT
    if was_truncated:
        raw_bytes = raw_bytes[:SOURCE_LIMIT]
    
    # Decode with replacement for invalid UTF-8
    raw_text = raw_bytes.decode("utf-8", errors="replace")
    
    # Check if this is an event stream
    is_stream = _is_event_stream(raw_text)
    
    if is_stream:
        # Try to extract assistant.message content
        message_content = None
        for event in event_objects(raw_text):
            msg = assistant_message(event)
            if msg:
                message_content = msg
                break
        
        if message_content:
            # Render markdown and sanitize
            md = markdown.Markdown(extensions=['tables'])
            html = md.convert(message_content)
            # Sanitize HTML to log-only allowlist
            html = nh3.clean(html, tags=LOG_TAGS)
            
            # Apply display limit to text
            display_text = message_content
            if len(display_text.encode("utf-8")) > DISPLAY_LIMIT:
                # Truncate at DISPLAY_LIMIT bytes, ensuring valid UTF-8
                display_bytes = display_text.encode("utf-8")[:DISPLAY_LIMIT]
                display_text = display_bytes.decode("utf-8", errors="ignore")
                was_truncated = True
            
            return LogPreview(
                text=display_text,
                content_html=html,
                truncated=was_truncated
            )
        else:
            # Event stream with no messages - return as plain text
            # If truncated JSON with no messages, don't render as markdown
            if was_truncated and raw_text.lstrip().startswith(("{", "[")):
                return LogPreview(
                    text=raw_text,
                    content_html=None,
                    truncated=True
                )
            
            # Apply display limit
            display_text = raw_text
            if len(display_text.encode("utf-8")) > DISPLAY_LIMIT:
                display_bytes = display_text.encode("utf-8")[:DISPLAY_LIMIT]
                display_text = display_bytes.decode("utf-8", errors="ignore")
                was_truncated = True
            
            return LogPreview(
                text=display_text,
                content_html=None,
                truncated=was_truncated
            )
    else:
        # Plain text file
        display_text = raw_text
        content_html = None
        
        # Check if this is stderr (ends with .err)
        is_stderr = path.suffix == ".err"
        
        # If truncated and looks like JSON, don't render as markdown
        if was_truncated and raw_text.lstrip().startswith(("{", "[")):
            # Apply display limit
            if len(display_text.encode("utf-8")) > DISPLAY_LIMIT:
                display_bytes = display_text.encode("utf-8")[:DISPLAY_LIMIT]
                display_text = display_bytes.decode("utf-8", errors="ignore")
                was_truncated = True
            
            return LogPreview(
                text=display_text,
                content_html=None,
                truncated=was_truncated
            )
        
        # Apply display limit
        if len(display_text.encode("utf-8")) > DISPLAY_LIMIT:
            display_bytes = display_text.encode("utf-8")[:DISPLAY_LIMIT]
            display_text = display_bytes.decode("utf-8", errors="ignore")
            was_truncated = True
            
            # stderr files don't get HTML even if small
            if is_stderr:
                content_html = None
            else:
                # Try to render as markdown
                md = markdown.Markdown(extensions=['tables'])
                content_html = md.convert(display_text)
                content_html = nh3.clean(content_html, tags=LOG_TAGS)
        else:
            # Didn't hit display limit - try markdown for non-stderr
            if not is_stderr:
                md = markdown.Markdown(extensions=['tables'])
                content_html = md.convert(display_text)
                content_html = nh3.clean(content_html, tags=LOG_TAGS)
        
        return LogPreview(
            text=display_text,
            content_html=content_html,
            truncated=was_truncated
        )
