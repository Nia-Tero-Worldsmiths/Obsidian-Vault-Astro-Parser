"""Module 4 -- Callouts.

Converts Obsidian callout blockquotes into the DOM the ITS theme expects, so
the vendored stylesheet styles them without a parallel implementation. The
theme's selectors are specific, and matching them exactly is the whole job:

    .callout.callout[data-callout~=infobox] > .callout-title > .callout-icon
    .callout.callout[data-callout~=infobox] > .callout-content
    .callout.callout[data-callout~=infobox] table td

This vault uses exactly one callout type -- 103 `infobox` blocks, none nested,
none indented, none folded, none carrying a title. The module still handles
titles, fold markers and metadata, because those cost little and the vault's
own templates are the likeliest source of new ones.

Two things that are easy to get wrong here:

**The opener has no space.** This vault writes `>[!infobox]`, not the
documented `> [!infobox]`. A regex requiring the space matches zero of them.

**Markdown inside the wrapper must stay markdown.** The callout body is mostly
tables. A `<div>` in a markdown file starts an HTML block that swallows
everything until a blank line, so the content is deliberately surrounded by
blank lines -- that ends the HTML block, lets the tables parse normally, and
the divs still nest correctly in the output because raw HTML passes through.
"""

from __future__ import annotations

import html
import re

from ..model import Note, VaultContext
from .base import TransformModule

# `>[!type]`, `> [!type]-`, `> [!type|metadata] Title`.
_OPENER = re.compile(
    r"^>[ \t]*\[!(?P<type>[^\]|]+)(?:\|(?P<metadata>[^\]]*))?\][ \t]*"
    r"(?P<fold>[-+])?[ \t]*(?P<title>.*)$"
)

_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


class CalloutsModule(TransformModule):
    name = "callouts"
    order = 40
    summary = "Convert >[!infobox] blocks to themed callout markup"
    stub = False

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        lines = note.body.split("\n")
        output: list[str] = []
        index = 0
        in_fence = False
        fence_marker = ""

        while index < len(lines):
            line = lines[index]

            # Track fenced code so a `>[!note]` inside a code sample is left
            # alone. Line-based rather than via `mdtext`, because a callout is
            # a block construct and chunk splitting would cut it in half.
            fence = _FENCE.match(line)
            if fence:
                if not in_fence:
                    in_fence, fence_marker = True, fence.group(1)[0]
                elif fence.group(1)[0] == fence_marker:
                    in_fence = False
                output.append(line)
                index += 1
                continue

            opener = None if in_fence else _OPENER.match(line)
            if opener is None:
                output.append(line)
                index += 1
                continue

            end = index + 1
            while end < len(lines) and lines[end].startswith(">"):
                end += 1

            body_lines = [_strip_quote(raw) for raw in lines[index + 1 : end]]
            output.extend(self._render(opener, body_lines))
            self.count(f"callout {opener.group('type').strip().lower()}")
            index = end

        note.body = "\n".join(output)
        return note

    def _render(self, opener: re.Match, body_lines: list[str]) -> list[str]:
        callout_type = opener.group("type").strip()
        metadata = (opener.group("metadata") or "").strip()
        fold = opener.group("fold")
        title = opener.group("title").strip()

        classes = ["callout"]
        if fold == "-":
            classes.append("is-collapsed")

        attributes = f' data-callout="{html.escape(callout_type.lower())}"'
        if metadata:
            attributes += f' data-callout-metadata="{html.escape(metadata)}"'

        # Obsidian falls back to the capitalised type name when no title is
        # given. The theme hides it unless `show-title` metadata is present,
        # but the element has to exist for those selectors to work.
        title_html = title or callout_type[:1].upper() + callout_type[1:]

        title_parts = ['<div class="callout-icon"></div>']
        title_parts.append(f'<div class="callout-title-inner">{title_html}</div>')
        if fold:
            title_parts.append('<div class="callout-fold"></div>')

        while body_lines and not body_lines[-1].strip():
            body_lines.pop()

        return [
            "",
            f'<div class="{" ".join(classes)}"{attributes}>',
            f'<div class="callout-title">{"".join(title_parts)}</div>',
            '<div class="callout-content">',
            "",
            *body_lines,
            "",
            "</div>",
            "</div>",
            "",
        ]


def _strip_quote(line: str) -> str:
    """Remove one level of blockquote marker.

    Only the first `>` -- infobox rows legitimately contain more, as in
    `> |└>Subclase| Arcane Trickster |`.
    """
    if not line.startswith(">"):
        return line
    rest = line[1:]
    return rest[1:] if rest.startswith(" ") else rest
