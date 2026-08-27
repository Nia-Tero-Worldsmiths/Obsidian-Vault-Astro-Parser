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

from .. import linking, zoommap
from ..model import Note, VaultContext, WikiRef
from ..zoommap import Icon, MapSpec, ZoommapError
from .base import TransformModule

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

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if "zoommap" not in note.body:
            return note
        if self._icons is None:
            self._icons = zoommap.load_icons(ctx.config)
            self.count("icons available", len(self._icons))

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

        asset = ctx.find_asset(spec.image_path)
        if asset is None:
            self.count("fences failed")
            self.warn(f"{note.vault_path}: map image not found: {spec.image_path}")
            return match.group(0)

        ctx.referenced_assets.add(asset.vault_path)
        url = f"{ctx.config.asset_base_url}/{asset.out_name}"

        for warning in spec.warnings:
            self.warn(f"{note.vault_path}: {warning}")

        self.count("maps rendered")
        self.count("pins rendered", len(spec.markers))
        return self._markup(spec, url, note, ctx)

    # -- markup ----------------------------------------------------------

    def _markup(self, spec: MapSpec, url: str, note: Note, ctx: VaultContext) -> str:
        pins = "".join(self._pin(marker, note, ctx) for marker in spec.markers)

        attributes = [
            'class="zoommap"',
            f'data-min-zoom="{spec.min_zoom:g}"',
            f'data-max-zoom="{spec.max_zoom:g}"',
        ]
        if spec.element_id:
            attributes.append(f'id="{html.escape(spec.element_id)}"')

        style = f"width:{html.escape(spec.css_width)}"
        if spec.css_height:
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
        return (
            "\n"
            f"<figure {' '.join(attributes)}>"
            '<div class="zoommap-viewport">'
            f'<div class="zoommap-world"{ratio}>'
            f'<img class="zoommap-base" src="{html.escape(url)}" alt="{alt}" '
            'draggable="false" />'
            f"{pins}"
            "</div></div>"
            '<div class="zoommap-controls">'
            '<button type="button" class="zoommap-btn" data-zoom="in" aria-label="Acercar">+</button>'
            '<button type="button" class="zoommap-btn" data-zoom="out" aria-label="Alejar">-</button>'
            '<button type="button" class="zoommap-btn" data-zoom="reset" aria-label="Restablecer">&#8634;</button>'
            "</div>"
            "</figure>\n"
        )

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
