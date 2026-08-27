"""Inline tags.

Turns `#Territorio` in prose into Obsidian's own tag markup, `<a class="tag">`,
which `src/styles/base.css` and the ITS theme between them style as a pill.
Until this runs they render as a literal hash followed by a word.

Tags are rendered rather than removed because that is what Obsidian shows --
except for workflow tags. `#WIP`, `#Borrar` and `#Cambiar` are notes-to-self
about the writing, not content, and publishing them exposes the author's
to-do list on the live site. Those are dropped, and the list is configurable:

    modules:
      cleanup:
        enabled: true
        hidden_tags: [WIP, Borrar, Cambiar, TODO]

Only one inline tag currently reaches a published page (`#Party`), because
almost every note carrying them is still a draft -- but the count grows with
every note published, and a literal `#Borrar` on a live page is the kind of
thing that gets noticed late.

`ingest` already merges inline tags into `note.tags`, so the query module can
select on them regardless of what this does to the prose.
"""

from __future__ import annotations

import html
import re

from .. import mdtext
from ..model import Note, VaultContext
from .base import TransformModule

# An inline tag. The lookbehind keeps it from firing mid-word or inside a
# markdown anchor link like `](#section)`, and requiring a leading letter means
# `#1` and ATX headings (`# Title`, `## Sub`) are never mistaken for tags.
_INLINE_TAG = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[]))(?<!\]\()\#(?P<tag>[A-Za-zÀ-ÿ][\wÀ-ÿ/-]*)"
)

DEFAULT_HIDDEN_TAGS = ("WIP", "Borrar", "Cambiar", "TODO")

#: `strip` removes inline tags from the prose; `render` keeps them as pills.
DEFAULT_MODE = "strip"


class CleanupModule(TransformModule):
    name = "cleanup"
    order = 50
    summary = "Remove inline tags from prose (or render them as pills)"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        configured = self.options.get("hidden_tags", DEFAULT_HIDDEN_TAGS)
        self._hidden = {str(tag).lstrip("#").casefold() for tag in configured}
        self._mode = str(self.options.get("mode", DEFAULT_MODE)).lower()
        if self._mode not in ("strip", "render"):
            self.warn(f"unknown mode `{self._mode}`; falling back to `{DEFAULT_MODE}`")
            self._mode = DEFAULT_MODE

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if "#" not in note.body:
            return note
        note.body = mdtext.map_prose(note.body, self._rewrite)
        # Leaving `#Party ` behind as a lone line would keep its blank line and
        # a stray paragraph; collapse what the removal emptied.
        if self._mode == "strip":
            note.body = _tidy(note.body)
        return note

    def _rewrite(self, chunk: str) -> str:
        def replace(match: re.Match) -> str:
            tag = match.group("tag")
            if self._mode == "strip" or tag.casefold() in self._hidden:
                self.count("tags removed")
                return ""
            self.count("tags rendered")
            escaped = html.escape(tag)
            return f'<a class="tag" data-tag="{escaped}">#{escaped}</a>'

        return _INLINE_TAG.sub(replace, chunk)


def _tidy(body: str) -> str:
    """Drop lines that stripping emptied, and collapse the gap they leave."""
    out: list[str] = []
    for line in body.split("\n"):
        # A line that is now blank but sits between two blanks would otherwise
        # become a widening run of empty lines.
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out)
