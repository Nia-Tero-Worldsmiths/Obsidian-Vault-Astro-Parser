"""Parsing `:::lang` blocks, and splitting a note into one body per language.

This is a second implementation of rules that live in exactly one other place --
the i18n Manager plugin's `src/syntax.ts` and `src/markdownProcessor.ts`. Where
the two could differ, this file follows the plugin, because the vault is
authored against what Obsidian shows. Every rule below cites its counterpart.

The plugin hides content with a CSS class and never rewrites a note. The parser
cannot do that: the site has to serve finished markdown, and several later
modules (`empty_headings` most of all) reason about document structure and
would misread a document holding three languages at once. So the split happens
here, at order 15, and everything downstream sees one coherent language.

`%%comments%%` are stripped before this runs (order 10) for the same reason
Obsidian strips them first: a commented-out marker must not open a block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Open marker. Capture group 1 is one or more space-separated language codes.
# Mirrors OPEN_PATTERNS / CLOSE_PATTERNS in the plugin's src/syntax.ts.
_OPEN = re.compile(r"^:::\s*lang\s+([\w-]+(?:\s+[\w-]+)*)\s*$")
_CLOSE = re.compile(r"^:::\s*$")

# A fence opens on up to three spaces of indent, then three or more backticks
# or tildes. The info string, if any, follows on the same line.
_FENCE = re.compile(r"^\s{0,3}(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")

#: Info string of the fence the plugin renders as an infobox.
INFOBOX_FENCE = "i18n-infobox"

#: Sentinel meaning "show everything, in every language at once".
ALL = "ALL"


@dataclass(frozen=True)
class LangBlock:
    """One `:::lang` … `:::` span, by line number."""

    #: Raw code string as authored, e.g. "es" or "es en" or "all".
    codes: str
    open_line: int
    #: -1 when the block was never closed; it then runs to end of file.
    close_line: int

    @property
    def is_open_ended(self) -> bool:
        return self.close_line < 0

    def contains(self, line: int) -> bool:
        """Whether `line` sits strictly inside the block, markers excluded."""
        if line <= self.open_line:
            return False
        return self.is_open_ended or line < self.close_line


class _FenceTracker:
    """Tracks whether a line sits inside a fenced code block.

    Ported from the plugin's `FenceTracker`. Without it, a fence that
    *documents* the syntax is parsed as real markup -- the ```markdown sample
    in the vault's own `_i18n-test.md` used to make its own code sample vanish
    on a language switch.

    A fence closes only on the same character, at a length at least that of the
    opening run, and with nothing after the marker. That is what stops a ``` run
    inside a ~~~ block (or a shorter run inside a longer one) from ending it
    early.
    """

    def __init__(self) -> None:
        self._char: str | None = None
        self._length = 0
        self._info = ""

    @property
    def info(self) -> str:
        """Info string of the fence currently open, or "" when outside one."""
        return self._info

    def consume(self, line: str) -> bool:
        """Feed one line. True when it is inside, or is part of, a fence."""
        match = _FENCE.match(line)
        if not match:
            return self._char is not None

        marker = match.group("marker")
        rest = match.group("rest")

        if self._char is None:
            self._char = marker[0]
            self._length = len(marker)
            self._info = rest.strip()
            return True

        # A closing fence may not carry an info string.
        if marker[0] == self._char and len(marker) >= self._length and not rest.strip():
            self._char = None
            self._length = 0
            self._info = ""
        return True


def _is_marker_line(line: str) -> bool:
    """Whether a line may be a marker at all.

    Markers must start at column 0. The plugin's `matchLanguageBlockOpen` trims
    before matching, which is right for its DOM stripper -- rendered text nodes
    carry their own whitespace -- but as a *line* rule it would also match a
    marker indented inside a list item or a four-space code block. The plugin
    gates on `isMarkerLine` for exactly this reason.
    """
    return not line[:1].isspace()


def match_open(line: str) -> str | None:
    """The codes on an opening marker, or None."""
    if not _is_marker_line(line):
        return None
    match = _OPEN.match(line.strip())
    return match.group(1).strip() if match else None


def is_close(line: str) -> bool:
    if not _is_marker_line(line):
        return False
    return bool(_CLOSE.match(line.strip()))


def parse_blocks(source: str) -> list[LangBlock]:
    """Find every `:::lang` block, in document order.

    Blocks never nest: while one is open only a close marker is looked for, so
    an inner `:::lang ja` stays literal body text and the first bare `:::`
    closes the outer block. That is the plugin's two-state machine, kept
    deliberately.
    """
    lines = source.split("\n")
    blocks: list[LangBlock] = []
    fences = _FenceTracker()
    open_codes: str | None = None
    open_line = -1

    for index, line in enumerate(lines):
        # Fences win over language markers, so documentation of the syntax
        # stays inert.
        if fences.consume(line) or not _is_marker_line(line):
            continue

        if open_codes is None:
            codes = match_open(line)
            if codes is not None:
                open_codes, open_line = codes, index
        elif is_close(line):
            blocks.append(LangBlock(codes=open_codes, open_line=open_line, close_line=index))
            open_codes = None

    if open_codes is not None:
        blocks.append(LangBlock(codes=open_codes, open_line=open_line, close_line=-1))

    return blocks


def code_list(codes: str) -> list[str]:
    return [code for code in codes.split() if code]


def lang_match(codes: str, active: str) -> bool:
    """Whether a block tagged `codes` shows when `active` is selected.

    Mirrors the plugin's `langMatch`: the `ALL` sentinel shows everything, and
    a block tagged `all` shows under every language.
    """
    if active == ALL:
        return True
    lowered = [code.casefold() for code in code_list(codes)]
    return "all" in lowered or active.casefold() in lowered


def available_languages(blocks: list[LangBlock], configured: tuple[str, ...]) -> list[str]:
    """Which configured languages a note actually carries content for.

    A block tagged `all` counts as every configured language, matching
    `extractAvailableLanguagesFromBlocks`. Order follows `configured` so the
    result is deterministic rather than dependent on authoring order.
    """
    present: set[str] = set()
    has_all = False
    for block in blocks:
        for code in code_list(block.codes):
            if code.casefold() == "all":
                has_all = True
            else:
                present.add(code.casefold())

    if has_all:
        return list(configured)
    return [code for code in configured if code.casefold() in present]


# --- splitting -------------------------------------------------------------

class _Kind(Enum):
    """Who sees a given line.

    An Enum rather than sentinel strings because a BLOCK verdict carries the
    block's raw codes alongside it, and a note is free to write `:::lang drop`
    -- bare strings would collide with the codes they sit next to.
    """

    DROP = "drop"  # a marker line: never emitted
    ALWAYS = "always"  # shared: emitted for every language
    DEFAULT_ONLY = "default"  # emitted only for the default language
    BLOCK = "block"  # emitted for the languages named alongside


#: One line's verdict: its kind, plus the block codes when kind is BLOCK.
LineClass = tuple[_Kind, str]


def classify_lines(source: str, blocks: list[LangBlock]) -> list[LineClass]:
    """Label every line with who should see it.

    The rule, from `buildEvaluateVisibility` and reimplemented independently in
    the plugin's `ui/outlineFilter.ts` (which is what confirms it is intended
    rather than accidental):

    * Above the *first* opening marker -- shared, in every language. This is
      the note's preamble.
    * Inside a block -- that block's languages.
    * At or after the first opening marker but outside every block -- the
      default language only. Note the threshold is a single line number, so
      this catches text sitting *between* two blocks just as much as text
      after the last one.
    * A marker line itself -- dropped.

    One exception, added by the plugin's `sectionDeclaresInfobox`: an unwrapped
    ```i18n-infobox fence is always shared, because the infobox re-renders
    itself per language and hiding it as "default-language content" would only
    ever be surprising. An infobox deliberately placed inside a block still
    follows that block.
    """
    lines = source.split("\n")
    if not blocks:
        # No markers at all: the whole note is default-language content. It is
        # only ever emitted for the default language, so `always` is right.
        return [(_Kind.ALWAYS, "")] * len(lines)

    first_open = blocks[0].open_line
    marker_lines = {block.open_line for block in blocks}
    marker_lines |= {block.close_line for block in blocks if not block.is_open_ended}

    # Which lines belong to an infobox fence, so they can be shared.
    infobox_lines: set[int] = set()
    fences = _FenceTracker()
    for index, line in enumerate(lines):
        inside = fences.consume(line)
        if inside and (fences.info == INFOBOX_FENCE or _closing_infobox(lines, index)):
            infobox_lines.add(index)

    out: list[LineClass] = []
    for index in range(len(lines)):
        if index in marker_lines:
            out.append((_Kind.DROP, ""))
            continue
        owner = next((block for block in blocks if block.contains(index)), None)
        if owner is not None:
            out.append((_Kind.BLOCK, owner.codes))
        elif index in infobox_lines or index < first_open:
            out.append((_Kind.ALWAYS, ""))
        else:
            out.append((_Kind.DEFAULT_ONLY, ""))
    return out


def _closing_infobox(lines: list[str], index: int) -> bool:
    """Whether `index` is the closing fence of an ```i18n-infobox block.

    `_FenceTracker.info` is cleared by the time the closing line is consumed,
    so the closer needs looking up separately or it would fall through to the
    default-language rule and split the fence in half.
    """
    if not _FENCE.match(lines[index]) or lines[index].strip().strip("`~"):
        return False
    tracker = _FenceTracker()
    for line in lines[:index]:
        tracker.consume(line)
    return tracker.info == INFOBOX_FENCE


def body_for(source: str, classes: list[LineClass], lang: str, default: str) -> str:
    """The body one language should see."""
    lines = source.split("\n")
    keep: list[str] = []
    for line, (kind, codes) in zip(lines, classes):
        if kind is _Kind.DROP:
            continue
        if kind is _Kind.ALWAYS:
            keep.append(line)
        elif kind is _Kind.DEFAULT_ONLY:
            if lang == ALL or lang.casefold() == default.casefold():
                keep.append(line)
        elif lang_match(codes, lang):
            keep.append(line)
    return _tidy("\n".join(keep))


def split_bodies(
    source: str,
    languages: list[str],
    default: str,
) -> dict[str, str]:
    """One finished body per language, keyed by code."""
    blocks = parse_blocks(source)
    classes = classify_lines(source, blocks)
    return {lang: body_for(source, classes, lang, default) for lang in languages}


def _tidy(body: str) -> str:
    """Collapse the blank runs that removing a language's lines opens up.

    Same shape as `cleanup._tidy`, kept separate because that one is private to
    its module and this one also has to trim the document ends.
    """
    out: list[str] = []
    for line in body.split("\n"):
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out).strip("\n")
