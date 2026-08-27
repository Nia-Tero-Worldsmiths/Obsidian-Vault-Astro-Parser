"""Links & embeds.

Rewrites every `[[wikilink]]` and `![[embed]]` in a note's body to inline HTML,
and resolves the links hiding in frontmatter values so `inline_dataview` and `dataview_queries` can use
them without resolving again.

Two decisions worth knowing about:

**Frontmatter is annotated, not rewritten.** `ubicacion: "[[Region de Tambler]]"`
stays exactly as authored in the emitted file, because `dataview_queries`'s queries
compare against the link *target* (`where ubicacion = this.file.link`).
Rewriting it to HTML would break that comparison. The resolution is attached to
each `WikiRef` instead, and `inline_dataview` renders it at substitution time.

**Links to unpublished notes become plain text, not dead links.** On this vault
that is the common case, not the exception -- most notes are still drafts. The
markup carries `.is-unpublished` so it can be styled, and flipping a note's
`publish` flag turns every link to it into a real link on the next run.
"""

from __future__ import annotations

import re
from collections import Counter

from .. import linking, mdtext
from ..linking import WIKILINK, LinkKind
from ..model import Note, VaultContext, WikiRef
from .base import TransformModule


class LinksModule(TransformModule):
    name = "links"
    order = 20
    summary = "Resolve wikilinks, aliases and image embeds to site URLs"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        # Inbound counts for notes that exist but are not published -- the
        # publishing to-do list surfaced at the end of the run.
        self._unpublished_hits: Counter[str] = Counter()
        self._missing_hits: Counter[str] = Counter()
        self._missing_sources: dict[str, str] = {}

    # -- per note --------------------------------------------------------

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        note.body = mdtext.map_prose(note.body, lambda chunk: self._rewrite(chunk, note, ctx))
        self._annotate_frontmatter(note, ctx)
        return note

    def _rewrite(self, chunk: str, note: Note, ctx: VaultContext) -> str:
        def replace(match: re.Match) -> str:
            ref = linking.ref_from_match(match)
            if ref is None:
                # The `[[]]` placeholder the templates leave behind. Drop it
                # rather than rendering an empty broken link.
                self.count("empty placeholder dropped")
                return ""

            resolution = linking.resolve(ref, ctx)
            ref.resolution = resolution
            self._record(ref, resolution, note)
            return linking.render(ref, resolution)

        return WIKILINK.sub(replace, chunk)

    def _annotate_frontmatter(self, note: Note, ctx: VaultContext) -> None:
        """Resolve frontmatter links in place, without altering the values."""
        for ref in note.frontmatter_refs:
            if not ref.target:
                continue
            ref.resolution = linking.resolve(ref, ctx)
            self.count(f"frontmatter {ref.resolution.kind.value}")

    # -- bookkeeping -----------------------------------------------------

    def _record(self, ref: WikiRef, resolution, note: Note) -> None:
        self.count(f"body {resolution.kind.value}")

        if resolution.kind is LinkKind.UNPUBLISHED and resolution.note is not None:
            self._unpublished_hits[resolution.note.vault_path] += 1
        elif resolution.kind in (LinkKind.MISSING, LinkKind.ASSET_MISSING):
            self._missing_hits[ref.target] += 1
            self._missing_sources.setdefault(ref.target, note.vault_path)

    # -- report ----------------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        self._report_publish_candidates(ctx)
        self._report_broken(ctx)

    def _report_publish_candidates(self, ctx: VaultContext) -> None:
        if not self._unpublished_hits:
            return

        lines = [
            "These notes exist in the vault and are linked from published pages,",
            "but are not published themselves, so those links render as plain text.",
            "Ranked by how many links point at them:",
            "",
        ]
        for vault_path, count in self._unpublished_hits.most_common():
            note = ctx.by_vault_path.get(_key(vault_path))
            title = note.title if note else vault_path.rsplit("/", 1)[-1]
            lines.append(f"  {count:3}x  {title}")
            lines.append(f"       {vault_path}")

        total = sum(self._unpublished_hits.values())
        lines.append("")
        lines.append(
            f"  {total} link(s) across {len(self._unpublished_hits)} note(s). "
            "Set `publish: true` on any of these and re-run."
        )
        self.section("Publish candidates (linked but unpublished)", lines)

    def _report_broken(self, ctx: VaultContext) -> None:
        if not self._missing_hits:
            return

        lines = [
            "Link targets that match nothing in the vault. These are genuinely",
            "broken -- a typo, a renamed note, or an unfilled template stub.",
            "",
        ]
        for target, count in self._missing_hits.most_common():
            lines.append(f"  {count:3}x  [[{target}]]")
            lines.append(f"       first seen in {self._missing_sources[target]}")
        self.section("Broken links", lines)


def _key(vault_path: str) -> str:
    from ..model import index_key

    return index_key(vault_path)
