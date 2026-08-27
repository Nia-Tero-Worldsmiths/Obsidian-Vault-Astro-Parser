"""Code-aware markdown text handling.

Obsidian does not render wikilinks, dataview expressions or callouts inside
code, and neither should we. Every module that rewrites body text should go
through `map_prose` so a future note containing ``[[Example]]`` in a code block
is left alone.

This vault currently has zero wikilinks inside code, so nothing depends on it
today -- but the vault has fenced `dataview` and `zoommap` blocks whose
contents are configuration, not prose, and `dataview_queries` will add more. Getting the
boundary right once, here, keeps every later module honest.
"""

from __future__ import annotations

import re
from typing import Callable, Iterator

# A fenced block: ``` or ~~~, closed by the same marker at line start.
# The opening fence may carry an info string (```dataview).
_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[^\n]*\n"
    r"(?:.*?\n)??"
    r"(?P=indent)(?P=fence)[ \t]*(?:\n|\Z)",
    re.DOTALL | re.MULTILINE,
)

# Inline code: one or more backticks, closed by the same run length.
_INLINE_CODE = re.compile(r"(?P<ticks>`+)(?P<code>.*?)(?P=ticks)", re.DOTALL)


TEXT = "text"
FENCE = "fence"
INLINE_CODE = "inline_code"


def iter_regions(text: str) -> Iterator[tuple[str, str]]:
    """Yield `(chunk, kind)` covering `text` exactly once, in order.

    `kind` is one of `TEXT`, `FENCE`, `INLINE_CODE`. Inline code is kept
    distinct from fenced code because they are opposites for our purposes:
    `links` must never touch either, while `inline_dataview` exists precisely to
    rewrite inline code (Obsidian spells inline dataview `` `=this.field` ``)
    and must still leave fences alone.
    """
    for outer, is_fence in _split(text, _FENCE):
        if is_fence:
            yield outer, FENCE
            continue
        for inner, is_inline in _split(outer, _INLINE_CODE):
            yield inner, INLINE_CODE if is_inline else TEXT


def _split(text: str, pattern: re.Pattern) -> Iterator[tuple[str, bool]]:
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            yield text[position : match.start()], False
        yield match.group(0), True
        position = match.end()
    if position < len(text):
        yield text[position:], False


def map_prose(text: str, transform: Callable[[str], str]) -> str:
    """Apply `transform` to the prose parts of `text`, leaving all code alone."""
    return "".join(
        transform(chunk) if kind == TEXT else chunk
        for chunk, kind in iter_regions(text)
    )


def map_inline_code(text: str, transform: Callable[[str], str | None]) -> str:
    """Apply `transform` to inline code spans, leaving prose and fences alone.

    `transform` receives the span's *contents* without the surrounding
    backticks, and returns replacement markup, or None to leave the span
    exactly as it was.
    """
    parts: list[str] = []
    for chunk, kind in iter_regions(text):
        if kind != INLINE_CODE:
            parts.append(chunk)
            continue
        match = _INLINE_CODE.fullmatch(chunk)
        replacement = transform(match.group("code")) if match else None
        parts.append(chunk if replacement is None else replacement)
    return "".join(parts)


def map_outside_fences(text: str, transform: Callable[[str], str]) -> str:
    """Apply `transform` everywhere except inside fenced code blocks.

    Weaker protection than `map_prose` on purpose: Obsidian's `%%comments%%`
    take precedence over inline code, so a comment wrapping an inline-code span
    (`%%> ``=this.imagen`` %%`) has to be strippable as one unit. Fenced blocks
    stay literal.
    """
    parts: list[str] = []
    buffer: list[str] = []

    for chunk, kind in iter_regions(text):
        if kind == FENCE:
            if buffer:
                parts.append(transform("".join(buffer)))
                buffer = []
            parts.append(chunk)
        else:
            buffer.append(chunk)

    if buffer:
        parts.append(transform("".join(buffer)))
    return "".join(parts)


def prose_only(text: str) -> str:
    """The text with code regions blanked out, for counting and analysis.

    Newlines inside removed fences are preserved so line numbers still line up.
    """
    parts = []
    for chunk, kind in iter_regions(text):
        parts.append("\n" * chunk.count("\n") if kind != TEXT else chunk)
    return "".join(parts)
