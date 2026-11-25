"""Zoommap.

Renders ```zoommap fences as pan-and-zoom maps with clickable pins.

Feasibility was measured before building this, and it is far smaller than the
plugin's 876 KB suggests: 2 maps, 8 pins between them, all of type `pin`, one
layer each, and coordinates already normalised to 0-1. The plugin's bulk is its
*editor* -- measurement tools, travel-time calculators, drawing, second-screen
mirroring -- none of which a read-only page needs. What ships here is a static
container plus a small pan/zoom script.

Pins are positioned by percentage inside a scaled wrapper, so they stay pinned
to the terrain as the map moves, and counter-scaled so the icons stay a legible
size rather than ballooning at 8x zoom.

Icons come from the plugin's own `data.json` as self-contained data-URI SVGs,
so no icon files need copying and nothing is fetched at runtime.

Not rendered, and reported rather than silently dropped: drawings, overlays and
text layers. One drawing exists on the Pandysia map; everything else is empty.
"""

from __future__ import annotations

import html
import re

from .. import infobox as ib
from .. import linking, zoommap
from ..model import Note, VaultContext, WikiRef
from ..zoommap import Base, Icon, MapSpec, ZoommapError
from .base import TransformModule

#: The `values:` vocabulary a base's name is translated through. A pseudo-field
#: -- no note has a `mapBase` frontmatter key -- but the shape is exactly the
#: one `values:` already serves for `especie` or `genero`: a term authored in
#: the vault's language, with a translation per language beside it.
BASE_VOCABULARY = "mapBase"

#: A CSS length the author wrote in pixels, which is the only unit whose
#: number can be turned into a ratio without a layout to measure it against.
_PIXELS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*px\s*$", re.IGNORECASE)


def _pixels(value: str | None) -> float:
    """The number in a `px` length, or 0 for anything else (`%`, `vh`, ...)."""
    match = _PIXELS.match(value or "")
    return float(match.group(1)) if match else 0.0


_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<ticks>`{3,})[ \t]*zoommap[ \t]*\n"
    r"(?P<body>.*?)"
    r"^(?P=indent)(?P=ticks)[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)


class ZoommapModule(TransformModule):
    name = "zoommap"
    order = 80
    summary = "Render ```zoommap fences as interactive pan/zoom maps"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        self._icons: dict[str, Icon] | None = None
        #: The shared translation table, for base names. Loaded lazily and
        #: only when a note actually has a map, like the icons above.
        self._table: ib.InfoboxTable | None = None
        #: The language the vault authors in, so a base name already in it is
        #: never looked up and never reported as untranslated.
        self._default_lang = ""

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if "zoommap" not in note.body:
            return note
        if self._icons is None:
            self._icons = zoommap.load_icons(ctx.config)
            self.count("icons available", len(self._icons))
        if self._table is None:
            self._table = ib.load_table(ctx.config)
            self._default_lang = ctx.config.i18n.default

        note.body = _FENCE.sub(lambda m: self._render(m, note, ctx), note.body)
        return note

    def _render(self, match: re.Match, note: Note, ctx: VaultContext) -> str:
        try:
            fence = zoommap.parse_fence(match.group("body"))
            spec = zoommap.build(fence, ctx.config)
        except ZoommapError as exc:
            self.count("fences failed")
            self.warn(f"{note.vault_path}: {exc}")
            # Leave the fence untouched rather than deleting the author's
            # configuration -- a visible raw block is a better failure than a
            # silently missing map.
            return match.group(0)

        # Every base is resolved, not just the first. A base that cannot be
        # found is dropped with a warning rather than failing the map -- but
        # if the *first* one is missing there is nothing to show, and the
        # fence stays on the page as the loudest possible report.
        resolved: list[tuple[Base, str]] = []
        for base in spec.bases:
            asset = ctx.find_asset(base.path)
            if asset is None:
                self.warn(f"{note.vault_path}: map image not found: {base.path}")
                continue
            ctx.referenced_assets.add(asset.vault_path)
            resolved.append((base, f"{ctx.config.asset_base_url}/{asset.out_name}"))

        if not resolved:
            self.count("fences failed")
            return match.group(0)

        for warning in spec.warnings:
            self.warn(f"{note.vault_path}: {warning}")

        self.count("maps rendered")
        self.count("pins rendered", len(spec.markers))
        if len(resolved) > 1:
            self.count("maps with more than one base")
            self.count("map bases rendered", len(resolved))
        return self._markup(spec, resolved, note, ctx)

    # -- markup ----------------------------------------------------------

    def _markup(
        self,
        spec: MapSpec,
        resolved: list[tuple[Base, str]],
        note: Note,
        ctx: VaultContext,
    ) -> str:
        pins = "".join(self._pin(marker, note, ctx) for marker in spec.markers)

        attributes = [
            'class="zoommap"',
            f'data-min-zoom="{spec.min_zoom:g}"',
            f'data-max-zoom="{spec.max_zoom:g}"',
        ]
        if spec.element_id:
            attributes.append(f'id="{html.escape(spec.element_id)}"')

        # The declared box, expressed so that CSS can shrink it. `max-width:
        # 100%` caps the width on a narrow screen, but a *fixed* height would
        # survive that cap and leave the map fitted inside a box too tall for
        # it. A ratio scales with the width instead, so the whole box shrinks
        # and the author's proportions are the ones that are kept.
        style = f"width:{html.escape(spec.css_width)}"
        declared_w = _pixels(spec.css_width)
        declared_h = _pixels(spec.css_height)
        if declared_w and declared_h:
            style += f";aspect-ratio:{declared_w:g} / {declared_h:g}"
        elif spec.css_height:
            # A height in some other unit -- `vh`, `%`, `em`. Nothing to build
            # a ratio out of, so it stays as written; the width cap still
            # applies, and such a box was already fluid in one direction.
            style += f";height:{html.escape(spec.css_height)}"
        elif spec.width and spec.height:
            # No explicit height: reserve the image's own proportions, so the
            # page does not reflow when a multi-megabyte map finally loads.
            style += f";aspect-ratio:{spec.width} / {spec.height}"
        attributes.append(f'style="{style}"')

        # The world is sized in the image's *natural* pixels, because that is
        # what the plugin's zoom levels are relative to: `maxZoom: 1` means
        # 100% of the source image, not "fit the container". Sizing the world
        # to the container instead would make Tambler's `maxZoom: 1` mean the
        # map could never be magnified at all.
        ratio = ""
        if spec.width and spec.height:
            ratio = f' style="width:{spec.width}px;height:{spec.height}px"'

        alt = html.escape(note.title)

        # Every base ships, stacked, with only the first one shown. Swapping
        # `src` would be less markup but would fetch a multi-megabyte image
        # at the moment of the click; stacking makes the switch instant and
        # costs nothing a reader of this page was not going to download the
        # first time they pressed the button anyway.
        bases = "".join(
            f'<img class="zoommap-base" src="{html.escape(url)}" alt="{alt}" '
            f'data-base="{index}" draggable="false"'
            f"{'' if index == 0 else ' hidden'} />"
            for index, (_, url) in enumerate(resolved)
        )

        return (
            "\n"
            f"<figure {' '.join(attributes)}>"
            '<div class="zoommap-viewport">'
            f'<div class="zoommap-world"{ratio}>'
            f"{bases}"
            f"{pins}"
            "</div></div>"
            f"{self._switcher(resolved, note)}"
            '<div class="zoommap-controls">'
            '<button type="button" class="zoommap-btn" data-zoom="in" aria-label="Acercar">+</button>'
            '<button type="button" class="zoommap-btn" data-zoom="out" aria-label="Alejar">-</button>'
            '<button type="button" class="zoommap-btn" data-zoom="reset" aria-label="Restablecer">&#8634;</button>'
            "</div>"
            "</figure>\n"
        )

    def _switcher(self, resolved: list[tuple[Base, str]], note: Note) -> str:
        """The control that changes which base is shown.

        Nothing at all for the single-base maps, which is every map in the
        vault but one -- so those notes emit exactly the markup they emitted
        before this existed.

        Placed at the top-left of the figure, which is where the author put
        the switch pin in Obsidian (`hudModeX: left`, `hudModeY: top`).

        The group's own `aria-label` is Spanish, like the zoom buttons beside
        it: an attribute cannot hold one string per language, and the rest of
        this figure's chrome made the same choice.
        """
        if len(resolved) < 2:
            return ""

        buttons = []
        for index, (base, _) in enumerate(resolved):
            label = base.name.strip() or str(index + 1)
            active = ' aria-pressed="true" class="zoommap-base-btn is-active"' if index == 0 else (
                ' aria-pressed="false" class="zoommap-base-btn"'
            )
            buttons.append(
                f'<button type="button"{active} data-base="{index}">'
                f"{self._label(label, note)}</button>"
            )

        return (
            '<div class="zoommap-bases" role="group" aria-label="Versión del mapa">'
            + "".join(buttons)
            + "</div>"
        )

    def _label(self, name: str, note: Note) -> str:
        """A base's name, in every language the note carries.

        All of them, in one button, with the inactive ones hidden by the same
        CSS that hides everything else -- *not* the active language only.

        That is the whole reason this is not simply `note.active_language`.
        The pipeline runs this module once per language body, and `emit`
        decides what to share between those bodies by comparing the rendered
        output: a figure that came out differently per language would be
        emitted once per language, and `_merge_languages` records what that
        costs -- the Tambler map rendered three times, pan and zoom remembered
        per language instead of per page, and the hidden copies measuring
        their container as zero and opening at the wrong magnification.

        So the figure stays byte-identical across the passes and only the
        text inside it varies. Monolingual notes get the bare string, which
        keeps every existing map's markup unchanged.
        """
        codes = [code for code in note.languages if code]
        if len(codes) < 2:
            return html.escape(name)

        return "".join(
            f'<span class="lang-section" data-lang="{html.escape(code)}">'
            f"{html.escape(self._translate(name, code))}</span>"
            for code in codes
        )

    def _translate(self, term: str, lang: str) -> str:
        """One base name in one language, through the shared vocabulary.

        `values:` in the vault's translation table, under `mapBase`. It lives
        there rather than in the fence because the zoom-map plugin rewrites
        the fence from its own model -- `obj.imageBases = bases.map(b => ...
        {path, name})` -- so any key added beside `name` is dropped the next
        time the author touches the map. The table is a file that plugin
        never opens.

        An untranslated name falls back to what the fence says, which is the
        vault's own language, and is reported once per term.
        """
        table = self._table
        if not term or not table or lang == self._default_lang:
            return term

        vocab = table.values.get(BASE_VOCABULARY)
        entry = vocab.get(term) if isinstance(vocab, dict) else None
        if isinstance(entry, dict) and entry.get(lang):
            return str(entry[lang])

        self.warn(
            f"no `values.{BASE_VOCABULARY}` translation of map base "
            f"`{term}` into `{lang}`; showing it untranslated"
        )
        return term

    def _pin(self, marker, note: Note, ctx: VaultContext) -> str:
        icon = (self._icons or {}).get(marker.icon_key.casefold())

        label = marker.tooltip or marker.link or ""
        resolution = None
        if marker.link:
            ref = WikiRef(raw=marker.link, target=marker.link)
            resolution = linking.resolve(ref, ctx)
            if not label:
                label = resolution.label
            self.count(f"pin link {resolution.kind.value}")

        position = f"left:{marker.x * 100:.4f}%;top:{marker.y * 100:.4f}%"
        if icon is not None:
            position += (
                f";--zm-ax:{icon.anchor_x:g}px;--zm-ay:{icon.anchor_y:g}px"
                f";--zm-size:{icon.size:g}px"
            )

        inner = (
            f'<img class="zoommap-icon" src="{html.escape(icon.url)}" alt="" />'
            if icon is not None
            else '<span class="zoommap-dot" aria-hidden="true"></span>'
        )
        if icon is None and marker.icon_key:
            self.warn(f"{note.vault_path}: unknown marker icon `{marker.icon_key}`")

        escaped = html.escape(label)
        caption = f'<span class="zoommap-label">{escaped}</span>' if label else ""

        # A pin whose target is published becomes a link; one pointing at an
        # unpublished or missing note stays a marker, matching how `links`
        # treats the same situation in prose.
        if resolution is not None and resolution.kind is linking.LinkKind.PUBLISHED:
            return (
                f'<a class="zoommap-pin" href="{html.escape(resolution.url or "")}" '
                f'style="{position}" title="{escaped}">{inner}{caption}</a>'
            )

        classes = "zoommap-pin is-static"
        tooltip = escaped
        if resolution is not None and resolution.kind is linking.LinkKind.UNPUBLISHED:
            classes += " is-unpublished"
            # Keep the place name -- on a map the pin's own label is the point --
            # and append the reason it is not a link.
            tooltip = f"{escaped} — {html.escape(linking.UNPUBLISHED_TITLE)}"
        return (
            f'<span class="{classes}" style="{position}" title="{tooltip}">'
            f"{inner}{caption}</span>"
        )
