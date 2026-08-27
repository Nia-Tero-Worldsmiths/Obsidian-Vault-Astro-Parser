"""Obsidian comments.

Strips `%%...%%`, which Obsidian hides from readers. 92 spans across 49 notes,
most of them unfinished section headings the author parked for later
(`%%#### Vestimenta y accesorios%%`).

**This runs first, before links and callouts, and that ordering is load-bearing
rather than cosmetic.** Obsidian removes comments before it parses block
structure, so a comment can sit in the middle of a construct without breaking
it. `Natia Blanka-Tourmaline.md` does exactly that:

    >[!infobox]
    ># **`=this.title`**
    %%> `=embed(link(this.imagen))`%%
    > || Informacion |

That third line does not start with `>`. Parse callouts first and the block
ends early, orphaning the rest of the infobox as a bare blockquote -- which is
precisely what happened before this module existed. Three callouts vault-wide
hit this, one of them on a published page.

The same argument applies to links: a commented-out `[[wikilink]]` must not
become a real link, so comments have to go before `links` as well.
"""

from __future__ import annotations

import re

from .. import mdtext
from ..model import Note, VaultContext
from .base import TransformModule

# A comment contained within one line.
_INLINE = re.compile(r"%%.*?%%")

# A line consisting only of `%%`, which is how Obsidian opens and closes a
# block comment.
_BLOCK_DELIMITER = re.compile(r"^[ \t]*%%[ \t]*$")


class CommentsModule(TransformModule):
    name = "comments"
    order = 10
    summary = "Strip %%comments%% before anything parses block structure"
    stub = False

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if "%%" not in note.body:
            return note
        note.body = mdtext.map_outside_fences(note.body, self._strip)
        return note

    def _strip(self, chunk: str) -> str:
        kept: list[str] = []
        in_block = False

        for line in chunk.split("\n"):
            # A bare `%%` line toggles a block comment. Handling blocks this
            # way rather than with a DOTALL regex matters: a pattern like
            # `%%.*?\n.*?%%` happily spans two *separate* single-line comments,
            # eating the closing `%%` of one and the opening `%%` of the next
            # and corrupting everything between them.
            if _BLOCK_DELIMITER.match(line):
                in_block = not in_block
                self.count("block delimiters")
                continue

            if in_block:
                self.count("block comment lines dropped")
                continue

            cleaned, removed = _INLINE.subn("", line)
            if removed:
                self.count("inline comments", removed)

            # A line that was *only* a comment is dropped entirely rather than
            # left blank. Leaving a blank line behind would terminate whatever
            # block the comment was sitting inside -- the exact failure this
            # module exists to prevent.
            if line.strip() and not cleaned.strip():
                self.count("comment-only lines dropped")
                continue

            kept.append(cleaned)

        return "\n".join(kept)
