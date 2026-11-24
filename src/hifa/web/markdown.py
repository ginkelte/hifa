"""Markdown rendering with an in-house allowlist.

`markdown-it-py` parses CommonMark with `html=False` (raw HTML is escaped, not interpreted);
the result is then walked with `html.parser` and every tag/attribute outside the allowlist is
dropped. The one element the walker adds is the `div` that lets a wide table scroll. Never
returns `None`.

A reference the caller resolved becomes an anchor of its own: `issue_ref_candidates` scans the
text with `domain/identifier.py`'s grammar and `render_markdown` writes
`<a class="issue-ref" href="/{TEAM}/issue/{CANONICAL}">{as written}</a>` for a candidate the
mapping carries (SPEC-070 §2.3.6, R30, D-081).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from hifa.domain.identifier import IDENTIFIER_PATTERN

MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
# The `commonmark` preset ships the `table` rule disabled: this turns on that rule and nothing
# else — `gfm-like` would also enable strikethrough, linkify and task lists.
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
    {("a", "href"), ("a", "class"), ("th", "style"), ("td", "style")}
)
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
# The only `style` kept, and only on `th`/`td`: an aligned column is the one place the renderer
# writes one (`:--`, `:-:`, `--:`). Anything else drops the attribute and keeps the cell's text.
ALLOWED_STYLES: frozenset[str] = frozenset(
    {"text-align:left", "text-align:center", "text-align:right"}
)
VOID_TAGS: frozenset[str] = frozenset({"br"})
#: The one `class` the walker keeps, and the one thing that grants an anchor a site-relative
#: `href`: an identifier reference `render_markdown` resolved through the caller's mapping. No
#: input can write it — `html=False` escapes an `<a class="issue-ref">` an author types,
#: commonmark has no attribute syntax and `linkify` stays off — so this module is its only writer.
#: Private because nothing outside this module reads it: the contract publishes the rendered
#: anchor, not the names the renderer spells it with (SPEC-070 §2.3.6, R30).
_ISSUE_REF_CLASS: str = "issue-ref"
#: The maximal run that can spell an identifier (SPEC-070 §2.3.6, R30): the run bounds the
#: candidate and `IDENTIFIER_PATTERN` decides whether it is one, so `HIFA-1034` is never
#: `HIFA-103` plus a `4` and `XHIFA-103` never fires after its `X`. Private for the same reason
#: as the class: `issue_ref_candidates` is the published way to ask.
_RUN_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9-]+")


def _safe_href(value: str) -> bool:
    """Only `http(s):` and `mailto:`; anything else loses the attribute."""
    scheme = urlparse(value.strip()).scheme.lower()
    return scheme in ALLOWED_SCHEMES


def _site_relative(value: str) -> bool:
    """Whether `value` is a path of this site: one leading `/` and nothing a browser re-reads.

    `//host/x` and `/\\host/x` are the two spellings a browser treats as another host with no
    scheme in front of them, so neither is this site's path and neither passes. Nor does a value
    carrying a character the browser strips before it parses the URL — TAB, CR, LF — because
    `/<TAB>/host/x` re-reads as `//host/x`, another host again; a space or a NUL belongs in no
    path either. So the rule is the plain one: one leading `/` and path characters only. The one
    exception R30 grants to an `issue-ref` anchor stays the canonical path it writes.
    """
    if not value.startswith("/") or value.startswith(("//", "/\\")):
        return False
    return not any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)


def _kept_value(
    tag: str, name: str, value: str | None, *, issue_ref_anchor: bool = False
) -> str | None:
    """Return the value to emit for an allowlisted attribute, or `None` when it is dropped.

    Each attribute is tested by the check it selects: `href` against the scheme allowlist, `style`
    against the three literal alignments, `class` against the single literal an identifier
    reference carries. Sharing the `href` test would drop every alignment, because
    `urlparse("text-align:center")` reads `text-align` as a scheme.

    Args:
        tag: The element the attribute sits on.
        name: The attribute's name, already lowercased by `html.parser`.
        value: The value as written, or `None` for a bare attribute (`<td style>`), which is
            dropped like any value outside the allowlist.
        issue_ref_anchor: Whether this anchor already carries `class="issue-ref"`. It is the one
            anchor whose `href` may be the site-relative canonical path (R30); every other anchor
            keeps the scheme rule.

    Returns:
        The `style` value stripped and lowercased — the normalized alignment, not the one the
        source wrote — the `href` and the `class` exactly as given, or `None` when the attribute is
        not kept.
    """
    if value is None or (tag, name) not in ALLOWED_ATTRS:
        return None
    if name == "style":
        alignment = value.strip().lower()
        return alignment if alignment in ALLOWED_STYLES else None
    if name == "class":
        return value if tag == "a" and value == _ISSUE_REF_CLASS else None
    if name == "href":
        if issue_ref_anchor and _site_relative(value):
            return value
        return value if _safe_href(value) else None
    return None


def _scanned(text: str) -> Iterator[tuple[int, int, str, str]]:
    """Every identifier candidate of `text` as `(start, end, as written, folded)`.

    The one scan this module owns: `issue_ref_candidates` folds and dedupes its output and the
    renderer uses the offsets to splice the anchor in, so the pattern and the fold are written
    once (SPEC-070 §2.3.6, R30).

    Args:
        text: The plain text of one markdown token.

    Yields:
        One tuple per maximal run of `[A-Za-z0-9-]` that `IDENTIFIER_PATTERN` accepts once folded.
    """
    for match in _RUN_PATTERN.finditer(text):
        written = match.group(0)
        folded = written.upper()
        if IDENTIFIER_PATTERN.fullmatch(folded) is not None:
            yield match.start(), match.end(), written, folded


def issue_ref_candidates(text: str | None) -> list[str]:
    """The folded distinct identifier candidates of one text, in the order they first appear.

    A candidate is a maximal run of `[A-Za-z0-9-]` that `IDENTIFIER_PATTERN.fullmatch` accepts
    once the run is upper-cased, so `HIFA-1034` is one candidate and never `HIFA-103` plus a `4`,
    and `XHIFA-103` is one candidate and never `HIFA-103` after an `X`. The caller resolves the
    candidates it gets; a candidate the mapping does not carry is never linked.

    Args:
        text: The text to scan, or `None` for a field the row leaves empty.

    Returns:
        The folded candidates, each once, in first-appearance order; `[]` for empty input.
    """
    if not text:
        return []
    return list(dict.fromkeys(folded for _, _, _, folded in _scanned(text)))


def _anchor(written: str, target: tuple[str, str]) -> Token:
    """One `issue_ref` token holding the anchor the mapping resolved.

    Args:
        written: The candidate as the author spelled it; it becomes the anchor's text, escaped.
        target: The canonical `(team_key, identifier)` the candidate resolves to.

    Returns:
        A token the renderer emits verbatim, because `MD` carries a rule for its type.

    Raises:
        ValueError: If `target` is not the two-string `(team_key, identifier)` pair the mapping
            promises — a value of another shape has no path to write, and the message names the
            candidate and the shape it had to be.
    """
    if (
        not isinstance(target, tuple)
        or len(target) != 2
        or not all(isinstance(part, str) for part in target)
    ):
        raise ValueError(
            f"candidate {written!r} resolved to {target!r},"
            " not the (team_key, identifier) pair the mapping promises"
        )
    team, identifier = target
    href = f"/{escape(team, quote=True)}/issue/{escape(identifier, quote=True)}"
    token = Token("issue_ref", "", 0)
    token.content = (
        f'<a class="{_ISSUE_REF_CLASS}" href="{href}">{escape(written, quote=False)}</a>'
    )
    return token


def _rewrite_text(token: Token, links: Mapping[str, tuple[str, str]]) -> list[Token]:
    """Split one `text` token around the candidates the mapping carries.

    Args:
        token: A `text` token of an inline run — never one inside `code_inline`, a fence or an
            existing link, because the caller does not descend into those.
        links: Candidate folded to upper case → canonical `(team_key, identifier)`.

    Returns:
        One token per run of text left alone and one anchor token per resolved candidate; the
        token itself when nothing resolves.
    """
    content = token.content
    pieces: list[Token] = []
    cursor = 0
    for start, end, written, folded in _scanned(content):
        target = links.get(folded)
        if target is None:
            continue
        if cursor < start:
            head = Token("text", "", 0)
            head.content = content[cursor:start]
            pieces.append(head)
        pieces.append(_anchor(written, target))
        cursor = end
    if not pieces:
        return [token]
    if cursor < len(content):
        tail = Token("text", "", 0)
        tail.content = content[cursor:]
        pieces.append(tail)
    return pieces


def _link_inline(token: Token, links: Mapping[str, tuple[str, str]]) -> None:
    """Rewrite the `text` children of one inline run, skipping every link's own text.

    Args:
        token: An `inline` token; its `children` may hold `code_inline` nodes (left untouched)
            and an existing link's `link_open`…`link_close` pair (whose text is not linked).
        links: Candidate folded to upper case → canonical `(team_key, identifier)`.
    """
    children = list(token.children or [])
    rewritten: list[Token] = []
    inside_link = 0
    for child in children:
        if child.type == "link_open":
            inside_link += 1
        elif child.type == "link_close":
            inside_link -= 1
        if child.type != "text" or inside_link:
            rewritten.append(child)
            continue
        rewritten.extend(_rewrite_text(child, links))
    token.children = rewritten


def _render_issue_ref(
    self: RendererHTML,
    tokens: Sequence[Token],
    idx: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    """Emit the anchor `_anchor` built, verbatim: the markdown-it arguments never touch it.

    The anchor's HTML is already in the token, so this rule reads nothing else: the token was
    built before rendering and carries its own target, its own text and its own escaping.

    Args:
        self: The renderer that owns the rule; unused, because the token holds the finished HTML.
        tokens: The inline run's tokens; `tokens[idx]` is the one this rule renders.
        idx: The index of that token in `tokens`.
        options: markdown-it's rendering options; unused, because `xhtmlOut` and `breaks` decide
            nothing about an anchor this module wrote.
        env: The render environment; unused, because the mapping was read out before rendering.

    Returns:
        The anchor's HTML, exactly as `_anchor` wrote it.
    """
    return tokens[idx].content


# `html_inline` is escaped under `html=False`, which is the point of that option, so the anchor
# travels as a token type of this module's own and is emitted by its own rule. Nothing an author
# writes can produce a token of this type: raw HTML arrives as text and is escaped.
MD.add_render_rule("issue_ref", _render_issue_ref)


class _Allowlist(HTMLParser):
    """Rebuilds the document keeping only the allowlisted subset, and adds the table's scroll box.

    A tag outside `ALLOWED_TAGS` is dropped whole; an attribute outside `ALLOWED_ATTRS` loses only
    itself and the tag survives. On top of the subset it keeps, the class emits the one `div` that
    lets a wide table scroll.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            return
        # The class is read before the attributes are filtered: it is what grants a site-relative
        # `href` to the one anchor that carries it, and nothing else can grant it (R30).
        issue_ref_anchor = tag == "a" and any(
            name == "class" and value == _ISSUE_REF_CLASS for name, value in attrs
        )
        kept = ""
        for name, value in attrs:
            emitted = _kept_value(tag, name, value, issue_ref_anchor=issue_ref_anchor)
            if emitted is not None:
                kept += f' {name}="{escape(emitted, quote=True)}"'
        if tag == "table":
            # The wrapper is the walker's own: `div` stays out of the allowlist, and this is the
            # only way a wide table gets a box to scroll in without `display: block` on the table.
            # Its balance rests on start/end pairing: markdown-it emits balanced tags and user
            # markup arrives escaped, so the wrapper cannot outlive its table.
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
                # Each `</table>` closes the wrapper its own `<table>` opened: the pairing is
                # the input's (balanced tags, escaped markup), not something tested in this method.
                self.parts.append("</div>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")


def render_markdown(
    text: str | None,
    links: Mapping[str, tuple[str, str]] | None = None,
) -> str:
    """Markdown to sanitized HTML (`""` for empty input, never `None`).

    `links` maps a candidate as the author wrote it, folded to upper case, to the canonical
    `(team_key, identifier)` of the issue it names; `None` renders exactly what this function
    rendered before the parameter existed. A candidate the mapping carries becomes
    `<a class="issue-ref" href="/{TEAM}/issue/{CANONICAL}">{as written}</a>` — the canonical target
    and the author's own spelling — and nothing inside `code_inline`, a fence, a code block or an
    existing link's text is ever linked (R30, D-081).

    Args:
        text: The markdown to render, or `None`.
        links: Candidate folded to upper case → canonical `(team_key, identifier)`, built by the
            caller that holds the connection; `None` (or empty) leaves every candidate as text.

    Returns:
        Escaped, allowlisted HTML; the empty string for empty input.
    """
    if text is None or not text.strip():
        return ""
    if links:
        tokens = MD.parse(text)
        for token in tokens:
            if token.type == "inline":
                _link_inline(token, links)
        rendered = MD.renderer.render(tokens, MD.options, {})
    else:
        rendered = MD.render(text)
    parser = _Allowlist()
    parser.feed(rendered)
    parser.close()
    return "".join(parser.parts)


__all__ = [
    "ALLOWED_ATTRS",
    "ALLOWED_SCHEMES",
    "ALLOWED_STYLES",
    "ALLOWED_TAGS",
    "MD",
    "issue_ref_candidates",
    "render_markdown",
]
