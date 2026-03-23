"""Tests for display title extraction from markdown body text."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agency.app import extract_display_title


class TestExtractDisplayTitle:

    def test_extracts_first_bold_text(self):
        body = "**Coverage map shows only 3 real categories** instead of the expected 8."
        assert extract_display_title(body, "fallback-slug") == "Coverage map shows only 3 real categories"

    def test_extracts_bold_with_numbers_and_special_chars(self):
        body = "**48 pending subscribers vs 38 active = 56% never complete double opt-in**\n\nMore details here."
        assert extract_display_title(body, "fallback-slug") == "48 pending subscribers vs 38 active = 56% never complete double opt-in"

    def test_strips_trailing_punctuation(self):
        body = "**The API latency has increased by 300%.**\n\nDetails follow."
        assert extract_display_title(body, "the-api-latency") == "The API latency has increased by 300%"

    def test_falls_back_to_slug_when_no_bold(self):
        body = "This is a body without any bold text.\n\nJust plain paragraphs."
        assert extract_display_title(body, "my-observation-slug") == "my observation slug"

    def test_falls_back_to_slug_when_empty_body(self):
        assert extract_display_title("", "some-slug") == "some slug"
        assert extract_display_title(None, "some-slug") == "some slug"

    def test_ignores_bold_inside_headings(self):
        body = "## **Heading Bold**\n\n**Actual first bold in body text** and more."
        assert extract_display_title(body, "fallback") == "Actual first bold in body text"

    def test_handles_multiline_before_bold(self):
        body = "Some intro text.\n\n**The real title is here** with context."
        assert extract_display_title(body, "fallback") == "The real title is here"

    def test_truncates_long_titles(self):
        long_text = "A" * 200
        body = f"**{long_text}** and more."
        result = extract_display_title(body, "fallback")
        assert len(result) <= 120
        assert result.endswith("...")

    def test_handles_bold_with_inline_code(self):
        body = "**The `cache_ttl` setting is misconfigured** across all environments."
        assert extract_display_title(body, "fallback") == "The `cache_ttl` setting is misconfigured"
