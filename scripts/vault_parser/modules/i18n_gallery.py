"""The ```i18n-gallery fence: a note's own images, laid out as a grid.

An infobox holds the pictures that *identify* a character -- one at a time,
behind a dropdown, with `defaultImageIndex` naming the one that stands for the
note everywhere else. That is the wrong shape for the rest of the art. A vault
that has five drawings of someone wants to show five drawings, not hide four of
them behind a control, and the images it wants to show are precisely the ones
the author chose to keep out of the infobox.

So this reads a second family of keys. A `persona` layout declares

    image: imagen        # the infobox: one picture at a time
    gallery: gallery     # this: all of them at once

and `resolve_images` serves both, because the family name is a parameter --
`gallery` brings `galleryLabel`, `gallerySize` and `galleryColumns` with it the
same way `image` brings `imageLabel` and `imageSize`. Labels pair by position
and translate through the shared `values:` vocabulary, so none of that is new
behaviour and none of it is written twice.

**Not a `groups:` entry**, which is where the query table's second use case
would go. A query selects *other notes* by tag or by a frontmatter key, and
both implementations skip the host note unconditionally; a gallery's rows are
one note's own images. That is an axis `members:` cannot express, and bending
it to fit would make the common case harder to read in order to avoid a module
that is sixty lines long.

**Not a table either.** `grouping.render_table` always emits a `<thead>`, its
exact output is a contract shared with `dataview_queries` and asserted
byte-for-byte, and a gallery has no columns to head. A list of captioned
pictures is a list of `<figure>`s, and says so.
"""

from __future__ import annotations

import html
import re
from typing import Any

from .. import infobox as ib, linking
from ..model import Note, VaultContext
from .base import TransformModule

_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*i18n-gallery[ \t]*\n"
    r"(?P<body>.*?)"
    r"^(?P=indent)(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

FENCE_NAME = "i18n-gallery"

#: The layout key family this fence reads. See `infobox.IMAGE_KEY`.
GALLERY_KEY = "gallery"

_KEYS = frozenset({"layout"})


class I18nGalleryModule(TransformModule):
    name = "i18n_gallery"
    # After `i18n_query` (62) and before `image_licensing` (82), so the images
    # this emits are licensed like any other.
    order = 63
    summary = "Render ```i18n-gallery fences as a grid of the note's own images"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        self._table: ib.InfoboxTable | None = None
        #: vault_path -> NoteType, for types whose layout declares no gallery.
        self._undeclared: dict[str, str] = {}
        #: Notes with a fence and nothing to put in it.
        self._empty: set[str] = set()
        #: vault_path -> the refs listed in both `imagen` and `gallery`.
        self._duplicated: dict[str, list[str]] = {}

    # -- per note --------------------------------------------------------

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if FENCE_NAME not in note.body:
            return note

        table = self._load(ctx)
        if not table.available:
            note.body = _FENCE.sub(lambda m: _warning(table.error), note.body)
            return note

        lang = note.active_language or ctx.config.i18n.default
        note.body = _FENCE.sub(
            lambda m: self._render(m.group("body"), note, ctx, table, lang), note.body
        )
        return note

    def _load(self, ctx: VaultContext) -> ib.InfoboxTable:
        if self._table is None:
            self._table = ib.load_table(ctx.config)
        return self._table

    # -- rendering -------------------------------------------------------

    def _render(
        self,
        source: str,
        note: Note,
        ctx: VaultContext,
        table: ib.InfoboxTable,
        lang: str,
    ) -> str:
        options = _parse_options(source)
        if isinstance(options, str):
            return _warning(options)

        note_type = options.get("layout") or note.frontmatter.get("NoteType")
        layout = table.layout_for(note_type)
        shown = str(note_type).strip() if note_type and str(note_type).strip() else "—"

        if layout is None:
            self._undeclared[note.vault_path] = shown
            return _warning(
                f"no layout for NoteType «{shown}»; "
                "add one under `layouts:` in the translation table"
            )
        if not layout.get(GALLERY_KEY):
            self._undeclared[note.vault_path] = shown
            return _warning(
                f"the «{shown}» layout declares no `{GALLERY_KEY}:` key, so there "
                "is nothing for this fence to read"
            )

        default = ctx.config.i18n.default
        images = ib.resolve_images(
            table, layout, note.frontmatter, lang, default, key=GALLERY_KEY
        )

        self._audit_overlap(note, table, layout, lang, default, images)

        title = ib.resolve_title(
            layout, note.frontmatter, lang, default, note.source.stem
        )
        figures = [f for f in (self._figure(note, ctx, image, title) for image in images) if f]

        if not figures:
            # Nothing to show is not an error: a character with no extra art
            # has an empty `gallery:`, and drawing an empty frame under the
            # author's heading would be worse than drawing nothing. Reported
            # instead, because the other cause is a misspelt filename.
            self._empty.add(note.vault_path)
            self.count("galleries with nothing to show")
            return ""

        self.count("galleries rendered")
        self.count("gallery images", len(figures))
        return f'\n<div class="ml-gallery"{_columns(layout)}>' + "".join(figures) + "</div>\n"

    def _figure(self, note: Note, ctx: VaultContext, image, title: str) -> str:
        if not image.ref:
            return ""
        # Unlike the infobox, an unlabelled gallery picture gets the note's
        # title as its alt text only because something must describe it; the
        # caption below is omitted rather than repeating that title visibly.
        tag = linking.image_tag(
            image.ref, ctx, alt=image.label or title, size=image.size
        )
        if tag is None:
            self.count("gallery image missing asset")
            note.warn(f"gallery image not found: {image.ref}")
            return ""

        caption = (
            f'<figcaption class="ml-gallery-caption">{html.escape(image.label)}</figcaption>'
            if image.label
            else ""
        )
        return f'<figure class="ml-gallery-item">{tag}{caption}</figure>'

    # -- audits ----------------------------------------------------------

    def _audit_overlap(
        self,
        note: Note,
        table: ib.InfoboxTable,
        layout: dict[str, Any],
        lang: str,
        default: str,
        images: list[ib.GalleryImage],
    ) -> None:
        """Images listed in both the infobox and the gallery.

        Almost always a mistake -- the point of the gallery key is to hold what
        the infobox does not -- and one that draws the same picture twice on
        the page without complaining. Comparing resolved refs rather than raw
        frontmatter strings, so `[[a.webp]]` and `a.webp` count as the same
        picture.
        """
        infobox_images = ib.resolve_images(table, layout, note.frontmatter, lang, default)
        shared = {i.ref for i in infobox_images} & {i.ref for i in images}
        if shared:
            self._duplicated[note.vault_path] = sorted(shared)
            self.count("images in both the infobox and the gallery", len(shared))

    # -- reporting -------------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        self._report_undeclared()
        self._report_duplicated()
        self._report_empty()

    def _report_undeclared(self) -> None:
        if not self._undeclared:
            return
        lines = [
            "These notes carry a gallery fence, but their NoteType has no",
            "layout, or a layout with no `gallery:` key. A warning was",
            "rendered in place.",
            "",
        ]
        for path, shown in sorted(self._undeclared.items())[:15]:
            lines.append(f"  {path}")
            lines.append(f"      NoteType: {shown}")
        self.section("Gallery: no gallery layout", lines)

    def _report_duplicated(self) -> None:
        if not self._duplicated:
            return
        lines = [
            "These notes list the same image in both the infobox key and the",
            "gallery key, so it is drawn twice. The gallery is for the pictures",
            "the infobox does not show.",
            "",
        ]
        for path, refs in sorted(self._duplicated.items())[:15]:
            lines.append(f"  {path}")
            for ref in refs:
                lines.append(f"      {ref}")
        self.section("Gallery: image shown twice", lines)

    def _report_empty(self) -> None:
        if not self._empty:
            return
        self.section(
            "Gallery: nothing to show",
            [
                "These notes carry a gallery fence that resolved to no images.",
                "Either the note has no extra art yet, or a filename is",
                "misspelt -- the two look identical from here.",
                "",
                *(f"  {path}" for path in sorted(self._empty)[:15]),
            ],
        )


def _columns(layout: dict[str, Any]) -> str:
    """The layout's column count, as an inline custom property.

    Inline rather than a class, because the count is a number the table may
    name freely and a stylesheet cannot enumerate. Absent means the grid falls
    back to its own responsive default, which is the right answer for most
    notes.
    """
    raw = layout.get(f"{GALLERY_KEY}Columns")
    if raw is None or not str(raw).strip().isdigit():
        return ""
    return f' style="--ml-gallery-columns: {int(str(raw).strip())}"'


def _parse_options(source: str) -> dict[str, str] | str:
    """The fence body: `layout:` and nothing else, or an error to show.

    Deliberately narrower than the query fence's grammar. `from:` and `not:`
    select among other notes, and there is no selecting to do here -- the
    images are the ones this note lists. Accepting them and ignoring them
    would be worse than refusing them.
    """
    options: dict[str, str] = {}
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            return f"`{line}` is not `key: value`"
        key = key.strip().casefold()
        if key not in _KEYS:
            return f"unknown option `{key}`; expected one of {', '.join(sorted(_KEYS))}"
        options[key] = value.strip().strip("\"'")
    return options


def _warning(message: str) -> str:
    return (
        f'\n<div class="ml-infobox-warning">'
        f'<span class="ml-infobox-warning-prefix">{FENCE_NAME}: </span>'
        f"<span>{html.escape(message)}</span></div>\n"
    )
