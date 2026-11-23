"""Markdown rendering with an in-house allowlist.

`markdown-it-py` parses CommonMark with `html=False` (raw HTML is escaped, not interpreted);
the result is then walked with `html.parser` and every tag/attribute outside the allowlist is
dropped. The one element the walker adds is the `div` that lets a wide table scroll. Never
returns `None`.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from markdown_it import MarkdownIt

MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
# The `commonmark` preset ships the `table` rule disabled: this turns on that rule and nothing
# else — `gfm-like` would also enable strikethrough, linkify and task lists (D-079).
MD.enable("table")

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
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    }
)
ALLOWED_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {("a", "href"), ("th", "style"), ("td", "style")}
)
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
# The only `style` kept, and only on `th`/`td`: an aligned column is the one place the renderer
# writes one (`:--`, `:-:`, `--:`). Anything else drops the attribute and keeps the cell's text.
ALLOWED_STYLES: frozenset[str] = frozenset(
    {"text-align:left", "text-align:center", "text-align:right"}
)
VOID_TAGS: frozenset[str] = frozenset({"br"})


def _safe_href(value: str) -> bool:
    """Only `http(s):` and `mailto:`; anything else loses the attribute."""
    scheme = urlparse(value.strip()).scheme.lower()
    return scheme in ALLOWED_SCHEMES


def _kept_value(tag: str, name: str, value: str | None) -> str | None:
    """The value to emit for an allowlisted attribute, or `None` when the attribute is dropped.

    Each attribute is tested by the check it selects: `href` against the scheme allowlist, `style`
    against the three literal alignments. Sharing the `href` test would drop every alignment,
    because `urlparse("text-align:center")` reads `text-align` as a scheme.
    """
    if value is None or (tag, name) not in ALLOWED_ATTRS:
        return None
    if name == "style":
        alignment = value.strip().lower()
        return alignment if alignment in ALLOWED_STYLES else None
    return value if _safe_href(value) else None


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
            kept_value = _kept_value(tag, name, value)
            if kept_value is not None:
                kept += f' {name}="{escape(kept_value, quote=True)}"'
        if tag == "table":
            # The wrapper is the walker's own: `div` stays out of the allowlist, and this is the
            # only way a wide table gets a box to scroll in without `display: block` on the table.
            self.parts.append('<div class="table-scroll">')
        self.parts.append(f"<{tag}{kept}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")
            if tag == "table":
                self.parts.append("</div>")

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


__all__ = [
    "ALLOWED_ATTRS",
    "ALLOWED_SCHEMES",
    "ALLOWED_STYLES",
    "ALLOWED_TAGS",
    "MD",
    "render_markdown",
]
