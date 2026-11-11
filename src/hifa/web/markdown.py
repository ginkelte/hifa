"""Markdown rendering with an in-house allowlist (SPEC-070 §2.3.6, D-020).

`markdown-it-py` parses CommonMark with `html=False` (raw HTML is escaped, not interpreted);
the result is then walked with `html.parser` and every tag/attribute outside the allowlist is
dropped. Never returns `None`.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from markdown_it import MarkdownIt

MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})

ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "br",
        "strong",
        "em",
        "code",
        "pre",
        "ul",
        "ol",
        "li",
        "a",
        "blockquote",
        "h1",
        "h2",
        "h3",
    }
)
ALLOWED_ATTRS: frozenset[tuple[str, str]] = frozenset({("a", "href")})
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
VOID_TAGS: frozenset[str] = frozenset({"br"})


def _safe_href(value: str) -> bool:
    """Only `http(s):` and `mailto:`; anything else loses the attribute (R15)."""
    scheme = urlparse(value.strip()).scheme.lower()
    return scheme in ALLOWED_SCHEMES


class _Allowlist(HTMLParser):
    """Rebuilds the document keeping only the allowlisted subset."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            return
        kept = ""
        for name, value in attrs:
            if (tag, name) in ALLOWED_ATTRS and value is not None and _safe_href(value):
                kept += f' {name}="{escape(value, quote=True)}"'
        self.parts.append(f"<{tag}{kept}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")


def render_markdown(text: str | None) -> str:
    """Markdown to sanitized HTML (`""` for empty input, never `None`)."""
    if text is None or not text.strip():
        return ""
    parser = _Allowlist()
    parser.feed(MD.render(text))
    parser.close()
    return "".join(parser.parts)


__all__ = ["ALLOWED_ATTRS", "ALLOWED_SCHEMES", "ALLOWED_TAGS", "MD", "render_markdown"]
