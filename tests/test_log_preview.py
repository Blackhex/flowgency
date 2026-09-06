import io
import json
from pathlib import Path

import pytest

from flowgency.web import log_preview


def test_historical_jsonl_recovers_messages_without_rewriting(tmp_path):
    path = tmp_path / "historic.out"
    raw = "\n".join([
        json.dumps({"type": "session.start", "data": {}}),
        '{broken',
        json.dumps({"type": "tool.execution_start", "data": {"arguments": "bad"}}),
        json.dumps({"type": "assistant.message", "data": {"content": "# Audit\n\n**Done**"}}),
    ])
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    preview = log_preview.read_log_preview(path)
    assert preview.text == "# Audit\n\n**Done**"
    assert "<h1>Audit</h1>" in preview.content_html
    assert "<strong>Done</strong>" in preview.content_html
    assert "tool.execution_start" not in preview.content_html
    assert path.read_bytes() == before


def test_unrelated_events_do_not_prevent_recovery(tmp_path):
    path = tmp_path / "mixed.out"
    path.write_text('null\n{"type":"unrelated"}\n{broken\n' + json.dumps({"type": "assistant.message", "data": {"content": "Recovered"}}), encoding="utf-8")
    preview = log_preview.read_log_preview(path)
    assert preview.text == "Recovered"
    assert preview.content_html == "<p>Recovered</p>"


def test_stream_without_messages_is_plain_text(tmp_path, monkeypatch):
    path = tmp_path / "events.out"
    raw = json.dumps({"type": "tool.execution_complete", "data": {"result": "<script>bad()</script>"}})
    path.write_text(raw, encoding="utf-8")
    def forbidden_markdown(*args, **kwargs):
        pytest.fail("Raw events must not enter Markdown")
    monkeypatch.setattr(log_preview.markdown, "Markdown", forbidden_markdown)
    preview = log_preview.read_log_preview(path)
    assert preview.content_html is None
    assert preview.text == raw


def test_markdown_with_json_example_remains_markdown(tmp_path):
    path = tmp_path / "report.out"
    path.write_text('# Report\n\n```json\n{"type":"assistant.message","data":{"content":"example"}}\n```\n\n- Item\n\n| A | B |\n|---|---|\n| 1 | 2 |', encoding="utf-8")
    html = log_preview.read_log_preview(path).content_html
    assert "<h1>Report</h1>" in html
    assert "<code" in html and "<li>Item</li>" in html and "<table>" in html


def test_markdown_sanitizes_active_markup(tmp_path):
    path = tmp_path / "unsafe.out"
    path.write_text('<script>window.logExecuted=1</script>\n<style>body{display:none}</style>\n<img src="/log-resource" onerror="bad()">\n<iframe src="/log-frame"></iframe>\n<form action="/danger"><input></form>\n<a href="javascript:bad()" onclick="bad()">bad</a>\n\n[safe](https://example.org)', encoding="utf-8")
    html = log_preview.read_log_preview(path).content_html
    for forbidden in ("<script", "<style", "<img", "<iframe", "<form", "<input", "onclick", "onerror", "javascript:"):
        assert forbidden not in html.lower()
    assert 'href="https://example.org"' in html


def test_source_reads_are_bounded_and_partial_record_ignored(monkeypatch):
    message = json.dumps({"type": "assistant.message", "data": {"content": "Kept"}}).encode() + b"\n"
    payload = message + b'{"type":"assistant.message","data":{"content":"' + b"x" * 200
    class TrackingStream(io.BytesIO):
        def read(self, size=-1):
            assert size == 129
            return super().read(size)
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 128)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: TrackingStream(payload))
    preview = log_preview.read_log_preview(Path("bounded.out"))
    assert preview.text == "Kept"
    assert preview.truncated


def test_cut_first_json_event_stays_inert(tmp_path, monkeypatch):
    path = tmp_path / "cut.out"
    path.write_text(json.dumps({"type": "tool.execution_complete", "data": {"result": "x" * 1000}}), encoding="utf-8")
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 128)
    preview = log_preview.read_log_preview(path)
    assert preview.content_html is None
    assert preview.truncated


@pytest.mark.parametrize("suffix", [".out", ".err"])
def test_display_limit_and_invalid_utf8(tmp_path, monkeypatch, suffix):
    path = tmp_path / f"unicode{suffix}"
    path.write_bytes(b"bad:\xff " + "\u00e9".encode() * 50)
    monkeypatch.setattr(log_preview, "DISPLAY_LIMIT", 31)
    preview = log_preview.read_log_preview(path)
    assert len(preview.text.encode("utf-8")) <= 31
    assert "\ufffd" in preview.text
    assert preview.truncated
    if suffix == ".err":
        assert preview.content_html is None


def test_large_event_stream_passes_only_message_text_to_markdown(tmp_path, monkeypatch):
    path = tmp_path / "large.out"
    envelope = json.dumps({"type": "tool.execution_complete", "data": {"result": "x" * 1024}})
    path.write_text((envelope + "\n") * 2800 + json.dumps({"type": "assistant.message", "data": {"content": "# Finished"}}), encoding="utf-8")
    original = log_preview.markdown.Markdown
    calls = []
    def converter(*args, **kwargs):
        instance = original(*args, **kwargs)
        convert = instance.convert
        def record(text):
            calls.append(text)
            return convert(text)
        instance.convert = record
        return instance
    monkeypatch.setattr(log_preview.markdown, "Markdown", converter)
    preview = log_preview.read_log_preview(path)
    assert calls == ["# Finished"]
    assert "<h1>Finished</h1>" in preview.content_html
    assert not preview.truncated


def test_multiple_assistant_messages_joined(tmp_path):
    path = tmp_path / "multi.out"
    path.write_text(
        json.dumps({"type": "assistant.message", "data": {"content": "First"}}) + "\n" +
        json.dumps({"type": "assistant.message", "data": {"content": "Second"}}),
        encoding="utf-8",
    )
    preview = log_preview.read_log_preview(path)
    assert "First" in preview.text
    assert "Second" in preview.text


def test_fenced_code_block_renders_as_pre_code(tmp_path):
    path = tmp_path / "fence.out"
    path.write_text(
        json.dumps({"type": "assistant.message", "data": {"content": "```\nhello\n```"}}) + "\n",
        encoding="utf-8",
    )
    html = log_preview.read_log_preview(path).content_html
    assert "<pre>" in html
    assert "<code>" in html


def test_newline_within_paragraph_becomes_br(tmp_path):
    path = tmp_path / "nl.out"
    path.write_text(
        json.dumps({"type": "assistant.message", "data": {"content": "line one\nline two"}}) + "\n",
        encoding="utf-8",
    )
    html = log_preview.read_log_preview(path).content_html
    assert "<br" in html


def test_ftp_url_rejected_by_sanitizer(tmp_path):
    path = tmp_path / "ftp.out"
    path.write_text(
        json.dumps({"type": "assistant.message", "data": {"content": "[get](ftp://files.example.org)"}}) + "\n",
        encoding="utf-8",
    )
    html = log_preview.read_log_preview(path).content_html
    assert "ftp://" not in html


def test_missing_log_raises_for_route_to_map(tmp_path):
    with pytest.raises(FileNotFoundError):
        log_preview.read_log_preview(tmp_path / "missing.out")


def test_exact_limits_do_not_claim_truncation(tmp_path, monkeypatch):
    path = tmp_path / "exact.err"
    path.write_bytes(b"12345678")
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 8)
    monkeypatch.setattr(log_preview, "DISPLAY_LIMIT", 8)
    preview = log_preview.read_log_preview(path)
    assert preview.text == "12345678"
    assert not preview.truncated


def test_plain_text_source_cut_inside_unicode_is_safe(tmp_path, monkeypatch):
    path = tmp_path / "partial.err"
    path.write_bytes(b"1234567" + "\u00e9".encode())
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 8)
    preview = log_preview.read_log_preview(path)
    assert preview.text == "1234567\ufffd"
    assert preview.truncated
