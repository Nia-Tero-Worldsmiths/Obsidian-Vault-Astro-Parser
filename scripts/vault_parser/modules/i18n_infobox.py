"""Plugin-rendered infobox.

Replaces a ```` ```i18n-infobox ```` fence with the same DOM the i18n Manager
plugin builds in Obsidian, so a note looks the same in both places. The
resolution logic lives in `infobox.py`, a straight port of the plugin's
`src/infobox/model.ts`; this module is only the pipeline half -- reading the
table once, rendering markup, and reporting what went wrong.

Order 65: after `dataview_queries` (60), so the legacy infobox has already been
built, and before `nav_tree` (70). Crucially also before `image_licensing` (82),
so a portrait this module emits is subject to the vault's publishing policy
like any other image.

**Both infobox formats are supported at once.** The legacy `>[!infobox]`
callout is `callouts` + `inline_dataview`; this is a separate construct and the
two never touch. That is what lets the vault convert a few notes at a time. The
`warn_legacy` option reports what is left to convert -- see `_report_legacy`.
"""

from __future__ import annotations

import html
import re

from .. import infobox as ib
from .. import linking
from ..infobox import normalize_type_key
from ..model import Note, VaultContext
from .base import TransformModule

# The fence this module owns. Matched the same way `dataview_queries` matches
# its own: indent-preserving, any fence length, so an indented block inside a
# list still resolves.
_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*i18n-infobox[ \t]*\n"
    r"(?P<body>.*?)^(?P=indent)(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

#: The legacy construct this one replaces, for the migration report.
_LEGACY = re.compile(r"^\s*>\s*\[!infobox\]", re.MULTILINE | re.IGNORECASE)


class I18nInfoboxModule(TransformModule):
    name = "i18n_infobox"
    order = 65
    summary = "Render ```i18n-infobox fences from NoteType and the shared table"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        self._table: ib.InfoboxTable | None = None
        self._warn_legacy = bool(self.options.get("warn_legacy", False))
        #: vault_path -> NoteType, for types the table has no layout for.
        self._unknown_types: dict[str, str] = {}
        #: Published notes still carrying a `>[!infobox]` callout.
        self._legacy: set[str] = set()
        #: vault_path -> messages, for rows that are structurally wrong.
        self._problems: dict[str, list[str]] = {}

    # -- per note --------------------------------------------------------

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if _LEGACY.search(note.raw_body):
            # `raw_body` rather than `body`: by now `callouts` has already
            # rewritten the callout into a <div>, but `raw_body` is never
            # mutated. It is also what makes this independent of module order.
            self._legacy.add(note.vault_path)

        if "i18n-infobox" not in note.body:
            return note

        table = self._load(ctx)
        if not table.available:
            note.body = _FENCE.sub(lambda m: _warning(table.error), note.body)
            return note

        lang = note.active_language or ctx.config.i18n.default
        note.body = _FENCE.sub(lambda m: self._render(note, ctx, table, lang), note.body)
        return note

    def _load(self, ctx: VaultContext) -> ib.InfoboxTable:
        if self._table is None:
            self._table = ib.load_table(ctx.config)
            if self._table.error:
                self.warn(self._table.error)
            else:
                self.count("layouts", len(self._table.layouts))
        return self._table

    def _render(
        self, note: Note, ctx: VaultContext, table: ib.InfoboxTable, lang: str
    ) -> str:
        note_type = note.frontmatter.get("NoteType")
        layout = table.layout_for(note_type)
        if layout is None:
            shown = str(note_type).strip() if isinstance(note_type, str) and note_type.strip() else "-"
            self._unknown_types[note.vault_path] = shown
            note.warn(f"no infobox layout for NoteType `{shown}`")
            return _warning(f"no layout for NoteType «{shown}»")

        default = ctx.config.i18n.default
        title = ib.resolve_title(layout, note.frontmatter, lang, default, note.source.stem)
        sections = ib.resolve_infobox(table, layout, note.frontmatter, lang, default)
        status = ib.resolve_status(table, layout, note.frontmatter, lang, default)
        ref, size = ib.resolve_image(table, layout, note.frontmatter, lang, default)
        if not ref:
            # The layout named no stand-in; the parser-side mapping is the
            # one that works without editing the vault's table.
            ref = ctx.config.fallback_images.get(
                normalize_type_key(note_type)
            ) or None
            if ref:
                self.count("portraits filled from fallback_images")

        for section in sections:
            for row in section.rows:
                if row.problem:
                    self._problems.setdefault(note.vault_path, []).append(row.problem)

        self.count("infoboxes rendered")
        return self._paint(note, ctx, title, status, ref, size, sections)

    # -- markup ----------------------------------------------------------

    def _paint(self, note, ctx, title, status, ref, size, sections) -> str:
        """Build the plugin's DOM.

        `data-callout="infobox"` is deliberate: it is what makes the vault
        theme's existing infobox rules apply unchanged. Note there is no
        `.callout-title` -- the plugin does not emit one, unlike the callout
        markup `callouts` produces for the legacy construct.
        """
        parts: list[str] = [
            '<div class="ml-infobox-host">',
            '<div class="callout ml-infobox" data-callout="infobox">',
            '<div class="callout-content">',
            self._title_html(title, status),
        ]

        image = self._image_html(note, ctx, ref, size, title)
        if image:
            parts.append(f'<div class="ml-infobox-image">{image}</div>')

        for section in sections:
            if not section.rows:
                # A section whose rows all vanished draws nothing at all.
                continue
            parts.append(self._table_html(section, ctx))

        parts += ["</div>", "</div>", "</div>"]

        # A `<div>` opens an HTML block that swallows everything to the next
        # blank line, so the wrapper needs blank lines around it or the
        # surrounding markdown stops parsing.
        return "\n" + "\n".join(parts) + "\n"

    def _title_html(self, title: str, status) -> str:
        marker = ""
        # A status with no symbol -- the ordinary "alive" case -- draws nothing.
        if status is not None and status.symbol:
            classes = "ml-infobox-status"
            if status.fallback:
                classes += " ml-infobox-status--fallback"
            tooltip = html.escape(status.tooltip)
            marker = (
                f'<sup class="{classes}" title="{tooltip}" aria-label="{tooltip}">'
                f"{html.escape(status.symbol)}</sup>"
            )
        return f'<h1 class="ml-infobox-title">{html.escape(title)}{marker}</h1>'

    def _image_html(self, note: Note, ctx: VaultContext, ref, size, title) -> str:
        if not ref:
            return ""
        resolved = linking.resolve_image(ref, ctx)
        if resolved is None:
            self.count("portrait missing asset")
            note.warn(f"infobox image not found: {ref}")
            return ""

        url, asset = resolved
        if asset is not None:
            ctx.referenced_assets.add(asset.vault_path)

        attrs = f'src="{html.escape(url)}" alt="{html.escape(title)}"'
        if size:
            width, _, height = str(size).partition("x")
            if width.strip().isdigit():
                attrs += f' width="{width.strip()}"'
            if height.strip().isdigit():
                attrs += f' height="{height.strip()}"'
        return f"<img {attrs} />"

    def _table_html(self, section, ctx: VaultContext) -> str:
        rows: list[str] = ['<table class="ml-infobox-table">']
        if section.heading:
            # Headings are markdown too, for symmetry with labels.
            heading, _ = linking.render_text_with_links(section.heading, ctx)
            rows.append(f"<thead><tr><th colspan=\"2\">{heading}</th></tr></thead>")

        rows.append("<tbody>")
        for row in section.rows:
            classes = []
            if row.missing:
                classes.append("ml-infobox-row--missing")
            if row.problem:
                classes.append("ml-infobox-row--problem")
            attrs = f' class="{" ".join(classes)}"' if classes else ""
            if row.problem:
                tooltip = html.escape(row.problem)
                attrs += f' title="{tooltip}" aria-label="{tooltip}"'

            # Labels are rendered as markdown so a label can be a wikilink --
            # the nación infobox links its "Nivel Tecnológico" row to the note
            # explaining tech levels, and `[[Nivel Tecnológico|技術レベル]]`
            # keeps that link while following the active language.
            label, _ = linking.render_text_with_links(row.label, ctx)
            rows.append(
                f"<tr{attrs}><td class=\"ml-infobox-label\">{label}</td>"
                f"<td class=\"ml-infobox-value\">{self._value_html(row, ctx)}</td></tr>"
            )
        rows += ["</tbody>", "</table>"]
        return "\n".join(rows)

    def _value_html(self, row, ctx: VaultContext) -> str:
        if row.missing:
            # Plain text, not markdown: the placeholder is a literal glyph, and
            # rendering it would let a value like "-" start a list.
            return html.escape(row.values[0])

        rendered, _ = linking.render_text_with_links(", ".join(row.values), ctx)
        parts = [html.escape(row.prefix) if row.prefix else "", rendered,
                 html.escape(row.suffix) if row.suffix else ""]
        return " ".join(part for part in parts if part)

    # -- report ----------------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        self._report_unknown_types()
        self._report_problems()
        self._report_legacy()

    def _report_unknown_types(self) -> None:
        if not self._unknown_types:
            return
        lines = [
            "These notes declare an infobox but their `NoteType` has no layout",
            "in the translation table, so the box renders as a warning on the",
            "live page. Add a layout under `layouts:` or drop the fence.",
            "",
        ]
        for path, note_type in sorted(self._unknown_types.items())[:20]:
            lines.append(f"  {path}")
            lines.append(f"      NoteType: {note_type}")
        if len(self._unknown_types) > 20:
            lines.append(f"  ... and {len(self._unknown_types) - 20} more")
        self.section("Infobox: unknown NoteType", lines)

    def _report_problems(self) -> None:
        if not self._problems:
            return
        lines = [
            "A grouped row has a child value with no parent at the same",
            "position -- a subclass with no class, say. The row is kept and",
            "marked on the page rather than hidden, so it can be fixed.",
            "",
        ]
        for path, messages in sorted(self._problems.items())[:20]:
            lines.append(f"  {path}")
            for message in sorted(set(messages)):
                lines.append(f"      {message}")
        if len(self._problems) > 20:
            lines.append(f"  ... and {len(self._problems) - 20} more")
        self.section("Infobox: mismatched paired rows", lines)

    def _report_legacy(self) -> None:
        """Burn-down for the migration off the hand-written infobox.

        The count is always reported, because it is the number worth watching
        and costs nothing. The list is opt-in: 118 unabridged paths would swamp
        the report while the migration has barely started.
        """
        if not self._legacy:
            return

        total = len(self._legacy)
        lines = [
            f"{total} published note(s) still use the hand-written",
            "`>[!infobox]` callout rather than the ```i18n-infobox fence.",
            "Both render, so this is progress tracking, not an error.",
        ]
        if not self._warn_legacy:
            lines += ["", "Set `warn_legacy: true` to list them."]
        else:
            lines.append("")
            for path in sorted(self._legacy)[:15]:
                lines.append(f"  {path}")
            if total > 15:
                lines.append(f"  ... and {total - 15} more")
        self.section("Legacy infoboxes remaining", lines)


def _warning(message: str) -> str:
    """Show the problem in place.

    Silence would look like a note that simply has no infobox, which is how a
    mistyped NoteType would go unnoticed for months.
    """
    return (
        '\n<div class="ml-infobox-warning">'
        '<span class="ml-infobox-warning-prefix">i18n-infobox: </span>'
        f"<span>{html.escape(message)}</span></div>\n"
    )
