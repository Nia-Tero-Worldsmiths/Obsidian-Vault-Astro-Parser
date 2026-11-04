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
        #: NoteType -> notes that declared a fence the type does not want.
        self._suppressed: dict[str, list[str]] = {}
        #: vault_path -> (written index, image count) for a stale default.
        self._bad_default: dict[str, tuple[object, int]] = {}
        #: vault_path -> (images, labels) where the two lists disagree.
        self._mislabelled: dict[str, tuple[int, int]] = {}
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

        if ib.suppressed(layout):
            # A decision, not a mistake: drop the block and say so in the run
            # report rather than on the page. A reader of a chapter should
            # never be shown scaffolding, and the author reading the report is
            # the one who can remove the stray fence.
            self._suppressed.setdefault(str(note_type).strip(), []).append(note.vault_path)
            self.count("infoboxes suppressed by layout")
            return ""

        default = ctx.config.i18n.default
        title = ib.resolve_title(layout, note.frontmatter, lang, default, note.source.stem)
        sections = ib.resolve_infobox(table, layout, note.frontmatter, lang, default)
        status = ib.resolve_status(table, layout, note.frontmatter, lang, default)
        images = ib.resolve_images(table, layout, note.frontmatter, lang, default)
        if not images:
            # The layout named no stand-in; the parser-side mapping is the
            # one that works without editing the vault's table.
            ref = ctx.config.fallback_images.get(ib.normalize_type_key(note_type))
            if ref:
                images = [ib.GalleryImage(ref)]
                self.count("portraits filled from fallback_images")

        self._audit_labels(note, layout, table, lang, default, images)

        for section in sections:
            for row in section.rows:
                if row.problem:
                    self._problems.setdefault(note.vault_path, []).append(row.problem)

        self.count("infoboxes rendered")
        return self._paint(note, ctx, title, status, images, sections)

    # -- markup ----------------------------------------------------------

    def _paint(self, note, ctx, title, status, images, sections) -> str:
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

        gallery = self._gallery_html(note, ctx, images, title)
        if gallery:
            parts.append(gallery)

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

    def _gallery_html(self, note: Note, ctx: VaultContext, images, title: str) -> str:
        """The image block: one picture, or a dropdown over several.

        Two rules, deliberately separate. **Alt text** follows from a label
        existing -- wherever a note named one, that image is described by it,
        whether or not a control is drawn. It replaces using the note title for
        every image, which told a screen-reader user nothing the heading above
        had not already said. **The control** follows from there being a
        choice, so a single image renders exactly the markup it always did and
        a note that merely labels its one portrait does not change shape.

        A dropdown rather than a row of buttons because the infobox column is
        about 290px wide and these labels are sentences -- "Forma humana (con
        Donatello)" alone is 182px of it. Two fit, barely; five would not, and
        a row that scrolls shows a reader one option at a time, which is the
        opposite of what a chooser is for. Collapsed, a dropdown is one line
        whatever the count.

        A native `<select>`, not a custom listbox: keyboard, screen reader and
        the mobile picker all come for free, where a hand-rolled one has to
        re-earn each of them.
        """
        rendered = [
            (image, self._img_tag(note, ctx, image, title)) for image in images
        ]
        rendered = [(image, tag) for image, tag in rendered if tag]
        if not rendered:
            return ""

        if len(rendered) == 1:
            return f'<div class="ml-infobox-image">{rendered[0][1]}</div>'

        index, ok = ib.default_image_index(note.frontmatter, len(rendered))
        if not ok and note.frontmatter.get(ib.DEFAULT_IMAGE_INDEX) is not None:
            self._bad_default[note.vault_path] = (
                note.frontmatter[ib.DEFAULT_IMAGE_INDEX],
                len(rendered),
            )
            note.warn(
                f"`{ib.DEFAULT_IMAGE_INDEX}` is not a position in this note's "
                f"{len(rendered)} image(s); showing the first"
            )

        # Derived from the note and language, never from a counter: a
        # per-render number would differ between builds and break the `dist/`
        # fingerprint CI uses to tell "nothing changed" from "something did".
        base = f"{note.slug}-{note.active_language or 'x'}-img"
        self.count("image galleries rendered")

        options: list[str] = []
        panels: list[str] = []
        for position, (image, tag) in enumerate(rendered):
            label = html.escape(image.label or f"{position + 1}")
            options.append(
                f'<option value="{position}"'
                f'{" selected" if position == index else ""}>{label}</option>'
            )
            # The caption is what the label looks like with no JavaScript: the
            # select is hidden until the script claims the gallery, so until
            # then this is a plain captioned list and every image is reachable.
            caption = (
                f'<span class="ml-infobox-gallery-caption">{label}</span>' if image.label else ""
            )
            panels.append(
                f'<div class="ml-infobox-gallery-panel" data-index="{position}">{tag}{caption}</div>'
            )

        return (
            '<div class="ml-infobox-image ml-infobox-gallery">'
            + f'<select class="ml-infobox-gallery-select" id="{base}-select"'
            + f' aria-label="{html.escape(title)}">'
            + "".join(options)
            + "</select>"
            + "".join(panels)
            + "</div>"
        )

    def _img_tag(self, note: Note, ctx: VaultContext, image, title: str) -> str:
        if not image.ref:
            return ""
        resolved = linking.resolve_image(image.ref, ctx)
        if resolved is None:
            self.count("portrait missing asset")
            note.warn(f"infobox image not found: {image.ref}")
            return ""

        url, asset = resolved
        if asset is not None:
            ctx.referenced_assets.add(asset.vault_path)

        # The label describes this picture; the title describes the note, and
        # is only worth repeating when there is nothing better.
        alt = image.label or title
        attrs = f'src="{html.escape(url)}" alt="{html.escape(alt)}"'
        if image.size:
            width, _, height = str(image.size).partition("x")
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

    def _audit_labels(self, note, layout, table, lang, default, images) -> None:
        """Note when labels and images do not line up.

        Not fatal -- a half-labelled gallery still renders, and the unlabelled
        buttons fall back to their position. But two parallel lists can drift,
        which is the one cost of this shape over a combined one, so it should
        never drift silently.
        """
        if len(images) < 2:
            return
        labels = ib.resolve_value(
            table, note.frontmatter, ib.label_key_for(layout), lang, default
        ) or []
        if len(labels) == len(images):
            return
        self._mislabelled[note.vault_path] = (len(images), len(labels))

    # -- report ----------------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        self._report_bad_default()
        self._report_mislabelled()
        self._report_suppressed()
        self._report_unknown_types()
        self._report_problems()
        self._report_legacy()

    def _report_bad_default(self) -> None:
        """A `defaultImageIndex` that names no image this note has.

        Reported rather than clamped, because the key exists to be deliberate:
        one left behind after images were removed should be seen. Note this
        catches only an index out of range -- reorder the list and a still-valid
        index quietly points somewhere else, which nothing here can detect.
        """
        if not self._bad_default:
            return
        lines = [
            f"`{ib.DEFAULT_IMAGE_INDEX}` is zero-based and must name one of the",
            "note's own images. These do not, so the first image was shown.",
            "",
        ]
        for path, (written, count) in sorted(self._bad_default.items())[:15]:
            lines.append(f"  {path}")
            lines.append(f"      wrote {written!r}, has {count} image(s)")
        if len(self._bad_default) > 15:
            lines.append(f"  ... and {len(self._bad_default) - 15} more")
        self.section("Infobox: default image out of range", lines)

    def _report_mislabelled(self) -> None:
        if not self._mislabelled:
            return
        lines = [
            "`imagen` and `imageLabel` are paired by position, and these notes",
            "give a different number of each. The extra images keep their place",
            "and fall back to a numbered button; the extra labels are dropped.",
            "",
        ]
        for path, (images, labels) in sorted(self._mislabelled.items())[:15]:
            lines.append(f"  {path}")
            lines.append(f"      {images} image(s), {labels} label(s)")
        if len(self._mislabelled) > 15:
            lines.append(f"  ... and {len(self._mislabelled) - 15} more")
        self.section("Infobox: images and labels do not line up", lines)

    def _report_suppressed(self) -> None:
        """Fences dropped because their NoteType declares `infobox: false`.

        Worth saying -- the block was authored and did nothing -- but not worth
        a warning box on a published page.
        """
        if not self._suppressed:
            return
        lines = [
            "These notes declare an infobox, but the translation table marks",
            "their `NoteType` as one that does not carry one (`infobox: false`).",
            "The block was dropped. Remove the fence, or give the type a layout.",
            "",
        ]
        for note_type, paths in sorted(self._suppressed.items()):
            lines.append(f"  NoteType: {note_type}  ({len(paths)} note(s))")
            for path in sorted(paths)[:5]:
                lines.append(f"      {path}")
            if len(paths) > 5:
                lines.append(f"      ... and {len(paths) - 5} more")
        self.section("Infobox not used by this NoteType", lines)

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
