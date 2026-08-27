"""Language blocks.

Splits a note carrying `:::lang` markers into one finished body per language,
so that every module after this one sees a document written in exactly one
language.

**Why split here rather than at the end.** The plugin hides other languages
with a CSS class and leaves the note alone, which works because Obsidian
re-renders live. A static site has to ship finished markdown, and more
importantly several later modules reason about document *structure*:
`empty_headings` decides whether a heading is empty by looking at what follows
it, and its rule is recursive. Handed a document holding three languages at
once it will happily delete a Spanish parent heading because the only
substantive child it could find was Japanese. Splitting first means that module
-- and `links`, and `dataview_queries` -- need no changes at all.

Order 15: after `comments` (10), before `links` (20). Comments must go first
for the same reason they precede everything else, and here it is load-bearing
twice over -- a `%%commented-out%%` marker must not open a block.

The syntax itself lives in `langblocks.py`, kept free of pipeline machinery so
it can be tested directly against the vault's own `_i18n-test.md`.
"""

from __future__ import annotations

from .. import langblocks
from ..model import Note, VaultContext
from .base import TransformModule


class LangBlocksModule(TransformModule):
    name = "lang_blocks"
    order = 15
    summary = "Split `:::lang` blocks into one body per language"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        #: vault_path -> codes, for notes naming a language nobody configured.
        self._unknown: dict[str, list[str]] = {}
        #: Notes with an unterminated block, which then runs to end of file.
        self._unclosed: list[str] = []
        #: vault_path -> codes whose content was dropped as not served.
        self._ignored: dict[str, set[str]] = {}
        #: Notes with prose stranded outside every block after the first one.
        self._stranded: list[str] = []

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        settings = ctx.config.i18n
        if not settings.enabled:
            return note

        configured = settings.served
        blocks = langblocks.parse_blocks(note.body)

        if not blocks:
            # No markers: the note is written entirely in the default language.
            # It still gets an entry so `emit` has one uniform shape to write.
            note.languages = [settings.default]
            note.lang_bodies = {settings.default: note.body}
            return note

        self._audit(note, blocks, settings)

        languages = langblocks.available_languages(blocks, configured)
        if settings.default not in languages:
            # A note translated into ja and en but never wrapped for es would
            # otherwise lose its default language entirely, and with it the
            # fallback every untranslated reader lands on.
            languages = [settings.default] + languages

        note.languages = languages
        note.lang_bodies = langblocks.split_bodies(note.body, languages, settings.default)
        # Keep `body` and the default entry the same object of truth: modules
        # that never learned about languages still read and write `body`.
        note.body = note.lang_bodies[settings.default]

        self.count("notes split")
        for code in languages:
            self.count(f"bodies: {code}")
        return note

    def _audit(self, note: Note, blocks: list, settings) -> None:
        """Collect the data-hygiene problems worth reporting on."""
        # A code the vault supports but the site does not serve is *known*: it
        # is a deliberate draft, not a mistake, and must not be reported as a
        # typo. Its content is dropped, which the ignored-content section
        # reports separately so the loss is never silent.
        known = {code.casefold() for code in settings.codes} | {"all"}
        served = {code.casefold() for code in settings.served}

        dropped = sorted(
            {
                code
                for block in blocks
                for code in langblocks.code_list(block.codes)
                if code.casefold() in known
                and code.casefold() not in served
                and code.casefold() != "all"
                and not any(
                    other.casefold() in served
                    for other in langblocks.code_list(block.codes)
                )
            }
        )
        if dropped:
            self._ignored.setdefault(note.vault_path, set()).update(dropped)
        unknown = sorted(
            {
                code
                for block in blocks
                for code in langblocks.code_list(block.codes)
                if code.casefold() not in known
            }
        )
        if unknown:
            self._unknown[note.vault_path] = unknown
            note.warn(f"unknown language code(s): {', '.join(unknown)}")

        if any(block.is_open_ended for block in blocks):
            self._unclosed.append(note.vault_path)
            note.warn("a `:::lang` block is never closed; it runs to end of file")

        if self._has_stranded_prose(note.body, blocks):
            self._stranded.append(note.vault_path)

    @staticmethod
    def _has_stranded_prose(body: str, blocks: list) -> bool:
        """Whether real prose sits outside every block, after the first one.

        Such text is treated as default-language content and silently
        disappears in every other language. That is the documented rule, but it
        is also the easiest mistake to make when converting a note, so it is
        worth naming rather than leaving to be noticed on the live site.

        Blank lines and an unwrapped infobox do not count -- the infobox is
        language-aware on its own.
        """
        classes = langblocks.classify_lines(body, blocks)
        lines = body.split("\n")
        for line, (kind, _codes) in zip(lines, classes):
            if kind.value == "default" and line.strip():
                return True
        return False

    def finalize(self, ctx: VaultContext) -> None:
        if not ctx.config.i18n.enabled:
            return
        self._report_ignored()
        self._report_unknown()
        self._report_unclosed()
        self._report_stranded()

    def _report_ignored(self) -> None:
        """What `i18n.ignore` left out.

        Not a problem -- it is the configured intent -- but dropping authored
        content silently would be, so the count is always shown.
        """
        if not self._ignored:
            return
        codes = sorted({code for codes in self._ignored.values() for code in codes})
        lines = [
            f"{len(self._ignored)} note(s) carry blocks in language(s) the site",
            f"does not serve ({', '.join(codes)}), so that content was not",
            "emitted. Remove the code from `i18n.ignore` to publish it.",
            "",
        ]
        for path, found in sorted(self._ignored.items())[:15]:
            lines.append(f"  {path}  [{', '.join(sorted(found))}]")
        if len(self._ignored) > 15:
            lines.append(f"  ... and {len(self._ignored) - 15} more")
        self.section("Languages not served", lines)

    def _report_unknown(self) -> None:
        if not self._unknown:
            return
        lines = [
            "These notes tag a block with a language that `i18n.languages` in",
            "vault.config.yaml does not list. The block is emitted for nobody.",
            "",
        ]
        for path, codes in sorted(self._unknown.items())[:20]:
            lines.append(f"  {path}")
            lines.append(f"      {', '.join(codes)}")
        if len(self._unknown) > 20:
            lines.append(f"  ... and {len(self._unknown) - 20} more")
        self.section("Unknown language codes", lines)

    def _report_unclosed(self) -> None:
        if not self._unclosed:
            return
        lines = [
            "A `:::lang` block here is never closed, so it swallows everything",
            "to the end of the note. Usually a missing `:::`.",
            "",
        ]
        lines += [f"  {path}" for path in sorted(self._unclosed)[:20]]
        if len(self._unclosed) > 20:
            lines.append(f"  ... and {len(self._unclosed) - 20} more")
        self.section("Unclosed language blocks", lines)

    def _report_stranded(self) -> None:
        if not self._stranded:
            return
        lines = [
            "These notes have prose sitting outside every `:::lang` block, at or",
            "below the first opening marker. It is treated as default-language",
            "content and vanishes in every other language -- which is the",
            "documented rule, and also the easiest thing to get wrong while",
            "converting a note. Wrap it, or move it above the first marker to",
            "share it across languages.",
            "",
        ]
        lines += [f"  {path}" for path in sorted(self._stranded)[:20]]
        if len(self._stranded) > 20:
            lines.append(f"  ... and {len(self._stranded) - 20} more")
        self.section("Prose outside a language block", lines)
