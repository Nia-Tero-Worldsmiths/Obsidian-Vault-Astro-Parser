"""Module 3 -- Inline dataview.

Substitutes Obsidian's inline dataview expressions with the note's own
frontmatter values. Until this runs, every infobox on the site reads
`=this.alineamiento` instead of `CG`.

The grammar in this vault is small and fully enumerated -- 1140 expressions
across exactly two shapes:

    `=this.<field>`               1064x, 23 distinct fields
    `=embed(link(this.imagen))`     76x, always this exact form

so this is a targeted substituter, not a dataview interpreter. Anything it
does not recognise is left untouched and counted, which makes an unsupported
expression visible in the report rather than silently blanked.

Values are rendered to match Obsidian's own output: null becomes `\\-` (the
vault's `renderNullAs` setting), lists join with commas, and wikilinks inside
values become real links via the shared resolver.
"""

from __future__ import annotations

import html
import re

from .. import linking, mdtext
from ..linking import parse_refs
from ..model import Note, VaultContext
from .base import TransformModule

# `=this.field`, allowing accented field names.
_FIELD = re.compile(r"^=\s*this\.(?P<field>[A-Za-zÀ-ÿ_][\w]*)\s*$")

# `=embed(link(this.field))` -- an image embed expressed as dataview.
_EMBED = re.compile(
    r"^=\s*embed\(\s*link\(\s*this\.(?P<field>[A-Za-zÀ-ÿ_][\w]*)\s*"
    r"(?:,[^)]*)?\)\s*\)\s*$"
)

#: What Obsidian shows for an empty property, per the vault's dataview config
#: (`renderNullAs: "\\-"`). Renders as a bare hyphen.
NULL_TEXT = r"\-"


class InlineDataviewModule(TransformModule):
    name = "inline_dataview"
    order = 30
    summary = "Substitute `=this.field` expressions with frontmatter values"
    stub = False

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        note.body = mdtext.map_inline_code(
            note.body, lambda code: self._evaluate(code, note, ctx)
        )
        return note

    def _evaluate(self, code: str, note: Note, ctx: VaultContext) -> str | None:
        """Return replacement markup, or None to leave the span as code."""
        expression = code.strip()
        if not expression.startswith("="):
            return None  # ordinary inline code, not a dataview expression

        embed = _EMBED.match(expression)
        if embed:
            self.count("embed")
            return self._render_embed(embed.group("field"), note, ctx)

        field = _FIELD.match(expression)
        if field:
            self.count("field")
            return self._render_field(field.group("field"), note, ctx)

        self.count("unsupported")
        self.warn(f"unsupported inline expression left as-is: `{expression}`")
        return None

    # -- rendering -------------------------------------------------------

    def _render_field(self, field: str, note: Note, ctx: VaultContext) -> str:
        value = self._lookup(field, note)
        if value is None:
            self.count("null")
            return NULL_TEXT
        return self._render_value(value, ctx)

    def _render_embed(self, field: str, note: Note, ctx: VaultContext) -> str:
        """`embed(link(this.imagen))` -> an <img>, or nothing when unset.

        Obsidian renders an empty embed here; emitting nothing is tidier than
        a broken image and keeps the infobox layout intact.
        """
        value = self._lookup(field, note)
        if not isinstance(value, str) or not value.strip():
            self.count("embed empty")
            return ""

        target = value.strip()
        # The value is either a bare filename (`Moby (Rondfort).jpg`) or a
        # wikilink (`[[Moby (Rondfort).jpg]]`). Reduce both to the filename.
        refs = parse_refs(target)
        if refs:
            target = refs[0].target

        asset = ctx.find_asset(target)
        if asset is None:
            self.count("embed missing asset")
            self.warn(f"image not found for `{field}`: {target}")
            return ""

        ctx.referenced_assets.add(asset.vault_path)
        url = f"{ctx.config.asset_base_url}/{asset.out_name}"
        alt = html.escape(note.title)
        return f'<img src="{html.escape(url)}" alt="{alt}" />'

    def _lookup(self, field: str, note: Note):
        """Read a field, preferring the note's effective title.

        `title` is special: 7 notes have no `title` frontmatter, so Obsidian
        renders `\\-` in their infobox heading while the page itself is named
        after the file. Using the effective title keeps the two consistent.
        """
        if field == "title":
            return note.title
        return note.frontmatter.get(field)

    def _render_value(self, value, ctx: VaultContext) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"

        if isinstance(value, list):
            rendered = [
                self._render_value(item, ctx) for item in value if item is not None
            ]
            rendered = [item for item in rendered if item]
            return ", ".join(rendered) if rendered else NULL_TEXT

        if not isinstance(value, str):
            return _escape_cell(str(value))

        text = value.strip()
        if not text:
            return NULL_TEXT

        rendered, resolutions = linking.render_text_with_links(text, ctx)
        for resolution in resolutions:
            self.count(f"link {resolution.kind.value}")
        return _escape_cell(rendered)


def _escape_cell(text: str) -> str:
    """Escape what would otherwise break the surrounding markdown table.

    Every one of these expressions sits inside an infobox table row, so an
    unescaped pipe in a value would silently add a column. Runs after link
    rendering because a rendered `<a>` never contains a bare pipe, while a
    raw value easily can.
    """
    return text.replace("|", r"\|")
