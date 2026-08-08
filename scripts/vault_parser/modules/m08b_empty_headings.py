"""Module 8b -- Empty headings.

Removes headings that promise content the note does not have. The vault's
templates lay out a full section skeleton (`### Historia`, `### Social`, ...)
that the author fills in over time, so a published note is often dotted with
headings that lead nowhere.

**The rule, bottom-up.** A heading is empty when both hold:

* nothing but blank lines sits between it and the next heading of any level;
* every heading nested beneath it is also empty.

So `### Historia` with an empty `#### Fundación` under it is empty and both
go, while `### Social` stays if any of its subsections has prose -- the parent
inherits substance from its children.

This replaces an earlier version that only trimmed the *tail* of a note.
Sections in the middle are just as empty and just as misleading, and the
recursive rule handles both without a special case.

**Why this runs near the end of the pipeline.** "Empty" is only knowable once
every module that *adds* content has run. A heading followed by nothing but a
```dataview fence looks empty until Module 6 executes the query; one followed
only by a `%%comment%%` or a `#WIP` tag becomes empty only after Modules 1b
and 5 remove those. Hence order 85, after queries (60) and zoommap (80).

Off by default: whether an empty heading is noise or a deliberate placeholder
is an editorial call.

    modules:
      empty_headings:
        enabled: true
        keep: ["Notas"]      # never removed, matched on heading text

Note this does trim glossary-style notes, where a heading *is* the entry and
the body is yet to be written -- `Santa Talla` is 20 rune names with no
definitions. That is the rule working as specified, not a bug; add such a
heading's text to `keep` if you want it spared.
"""

from __future__ import annotations

import re

from ..model import Note, VaultContext
from .base import TransformModule

# An ATX heading. Setext headings (underlined with === or ---) are absent from
# this vault and are not handled.
_HEADING = re.compile(r"^\s{0,3}(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*#*\s*$")

_FENCE = re.compile(r"^\s*(?P<ticks>```|~~~)")


class EmptyHeadingsModule(TransformModule):
    name = "empty_headings"
    order = 85
    summary = "Drop headings that have no content under them, at any depth"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        self._keep = {
            _plain_text(str(text)).casefold() for text in self.options.get("keep", ())
        }

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        lines = note.body.split("\n")
        headings = _find_headings(lines)
        if not headings:
            return note

        drop = self._empty_indices(lines, headings)
        if not drop:
            return note

        removed = [headings[k][2] or "(untitled)" for k in sorted(drop)]
        note.body = _rebuild(lines, headings, drop)

        self.count("headings removed", len(removed))
        self.count("notes trimmed")
        note.warn("removed empty heading(s): " + ", ".join(removed))
        return note

    def _empty_indices(self, lines: list[str], headings: list) -> set[int]:
        """Indices into `headings` that should be dropped.

        Walks backwards so a parent is judged after its children: by the time
        a heading is considered, every heading nested under it has already been
        classified, which is what makes "empty unless a child has substance"
        a single pass rather than a recursion.
        """
        empty: set[int] = set()

        for k in range(len(headings) - 1, -1, -1):
            start, level, text = headings[k]
            if _plain_text(text).casefold() in self._keep:
                continue

            end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
            if _has_content(lines[start + 1 : end]):
                continue

            # Any descendant with substance keeps the parent alive.
            substantive_child = False
            j = k + 1
            while j < len(headings) and headings[j][1] > level:
                if j not in empty:
                    substantive_child = True
                    break
                j += 1
            if substantive_child:
                continue

            empty.add(k)

        return empty


def _find_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, text) for every heading outside fenced code."""
    found: list[tuple[int, int, str]] = []
    in_fence = False
    marker = ""

    for index, line in enumerate(lines):
        fence = _FENCE.match(line)
        if fence:
            if not in_fence:
                in_fence, marker = True, fence.group("ticks")[0]
            elif fence.group("ticks")[0] == marker:
                in_fence = False
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            found.append((index, len(match.group("hashes")), match.group("text").strip()))

    return found


def _has_content(block: list[str]) -> bool:
    """True if the block holds anything other than blank lines.

    Fenced blocks count as content even when their contents look blank -- by
    this point in the pipeline a surviving fence is deliberate.
    """
    in_fence = False
    marker = ""
    for line in block:
        fence = _FENCE.match(line)
        if fence:
            if not in_fence:
                in_fence, marker = True, fence.group("ticks")[0]
            elif fence.group("ticks")[0] == marker:
                in_fence = False
            return True
        if in_fence or line.strip():
            return True
    return False


def _rebuild(lines: list[str], headings: list, drop: set[int]) -> str:
    """Remove the dropped headings and the blank runs they leave behind."""
    removed: set[int] = set()
    for k in drop:
        start = headings[k][0]
        end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
        removed.update(range(start, end))

    kept = [line for index, line in enumerate(lines) if index not in removed]

    # Collapse the multi-blank gaps that removal opens up.
    tidied: list[str] = []
    for line in kept:
        if not line.strip() and tidied and not tidied[-1].strip():
            continue
        tidied.append(line)

    return "\n".join(tidied).rstrip("\n") + "\n"


def _plain_text(text: str) -> str:
    """Strip the markup a heading may carry, so `keep` can match on words."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`]", "", text)
    return text.strip()
