"""Shared wikilink resolution and rendering.

`links` rewrites body links, `inline_dataview` renders frontmatter links inside
`=this.field` substitutions, and `dataview_queries` renders query results -- all three
must agree on what `[[Capital del Mar]]` resolves to and how it looks. That
agreement lives here rather than in any one module.

Rendered markup mirrors Obsidian's own DOM (`a.internal-link`,
`.is-unresolved`) so the site's stylesheets style these without a parallel
implementation -- `src/styles/base.css` binds them, the ITS theme colours them.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from .config import Config
from .model import Asset, Note, VaultContext, WikiRef

#: The canonical wikilink pattern: `[[target#heading^block|alias]]`, with an
#: optional leading `!` marking an embed. Every module parses links through
#: this one definition -- it used to be copied into three files, which is
#: exactly the kind of thing that drifts.
WIKILINK = re.compile(
    r"""(?P<embed>!)?\[\[
    (?P<target>[^\[\]|#^]*)
    (?:\#(?P<heading>[^\[\]|^]*))?
    (?:\^(?P<block>[^\[\]|]*))?
    (?:\|(?P<alias>[^\[\]]*))?
    \]\]""",
    re.VERBOSE,
)


def iter_refs(
    text: str, *, origin_key: str | None = None
) -> Iterator[tuple[re.Match, WikiRef]]:
    """Yield `(match, ref)` for each wikilink, so callers keep positions.

    Position-aware because a caller that needs to escape the prose *around*
    links cannot do it from the refs alone.
    """
    for match in WIKILINK.finditer(text):
        ref = ref_from_match(match, origin_key=origin_key)
        if ref is not None:
            yield match, ref


def parse_refs(text: str, *, origin_key: str | None = None) -> list[WikiRef]:
    return [ref for _, ref in iter_refs(text, origin_key=origin_key)]


def ref_from_match(match: re.Match, *, origin_key: str | None = None) -> WikiRef | None:
    """Build a `WikiRef`, or None for the empty `[[]]` template placeholder."""
    target = (match.group("target") or "").strip()
    heading = _strip_or_none(match.group("heading"))
    block = _strip_or_none(match.group("block"))
    if not target and not heading and not block:
        return None
    return WikiRef(
        raw=match.group(0),
        target=target,
        alias=_strip_or_none(match.group("alias")),
        heading=heading,
        block=block,
        is_embed=match.group("embed") == "!",
        origin_key=origin_key,
    )


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


#: Shown on links whose target exists in the vault but is not published. Set
#: here rather than at each call site so prose, infobox fields, query results
#: and map pins all say the same thing.
UNPUBLISHED_TITLE = "Esta página no ha sido publicada aún"


class LinkKind(str, Enum):
    PUBLISHED = "published"  # a note that ships -> real link
    UNPUBLISHED = "unpublished"  # a real note, not published yet -> plain text
    MISSING = "missing"  # nothing in the vault matches -> plain text
    ASSET = "asset"  # an image
    ASSET_MISSING = "asset_missing"
    EXTERNAL = "external"  # http(s), left alone


@dataclass(frozen=True)
class Resolution:
    kind: LinkKind
    label: str
    url: str | None = None
    note: Note | None = None
    asset: Asset | None = None

    @property
    def is_link(self) -> bool:
        return self.kind in (LinkKind.PUBLISHED, LinkKind.ASSET, LinkKind.EXTERNAL)


def display_text(ref: WikiRef) -> str:
    """What Obsidian shows for a link.

    An explicit alias wins. Otherwise it is the target's basename, so a
    full-path link like `[[World Encyclopedia/Ciencia y Tecnologia/Galo]]`
    reads as `Galo` rather than dumping the whole path into the prose.
    """
    if ref.alias:
        return ref.alias
    target = ref.target.rsplit("/", 1)[-1]
    return target or ref.target


def is_asset_reference(target: str, config: Config) -> bool:
    if "." not in target:
        return False
    suffix = "." + target.rsplit(".", 1)[-1].lower()
    return suffix in config.asset_extensions


def resolve_image(target: str, ctx: VaultContext) -> tuple[str, Asset | None] | None:
    """Resolve an image target to a `(url, asset)` pair, or None if unresolved.

    Tries a real vault asset first, then a vendored icon-library SVG (Font
    Awesome, RPG Awesome), inlined as a data URI -- the same treatment `zoommap`
    gives map pin icons. `asset` is None for an icon: unlike a real asset,
    nothing needs copying to `public/`, and callers must not add it to
    `ctx.referenced_assets`.

    Shared so `links` (here), `inline_dataview`'s frontmatter embeds and `dataview_queries`'s
    query portraits all resolve `imagen: castle-flag.svg` the same way.
    """
    asset = ctx.find_asset(target)
    if asset is not None:
        return f"{ctx.config.asset_base_url}/{asset.out_name}", asset
    icon = ctx.find_icon(target)
    if icon is not None:
        return icon, None
    return None


def resolve(ref: WikiRef, ctx: VaultContext) -> Resolution:
    """Resolve one reference against the vault index."""
    config = ctx.config
    label = display_text(ref)
    target = ref.target.strip()

    if not target:
        return Resolution(LinkKind.MISSING, label)

    if "://" in target:
        return Resolution(LinkKind.EXTERNAL, label, url=target)

    if is_asset_reference(target, config):
        resolved = resolve_image(target, ctx)
        if resolved is None:
            return Resolution(LinkKind.ASSET_MISSING, label)
        url, asset = resolved
        if asset is not None:
            # Record the usage: whatever a module points an <img> at must be
            # copied, even when the source text no longer shows the reference
            # afterwards. An icon needs no such record -- it is never copied.
            ctx.referenced_assets.add(asset.vault_path)
        return Resolution(LinkKind.ASSET, label, url=url, asset=asset)

    note = ctx.find_note(target)
    if note is None:
        return Resolution(LinkKind.MISSING, label)
    if not note.published:
        return Resolution(LinkKind.UNPUBLISHED, label, note=note)
    return Resolution(LinkKind.PUBLISHED, label, url=note.url, note=note)


def render_note(note: Note, *, label: str | None = None) -> str:
    """Render a link to a known note, honouring the publish gate.

    For callers that already hold a `Note` -- query results, navigation -- and
    so have nothing to resolve. Keeps published/unpublished markup identical to
    links written by hand in prose.
    """
    text = html.escape(label or note.title)
    if note.published:
        return f'<a class="internal-link" href="{html.escape(note.url)}">{text}</a>'
    return f'<span class="internal-link is-unpublished">{text}</span>'


def render_text_with_links(text: str, ctx: VaultContext) -> tuple[str, list[Resolution]]:
    """Render a string that may contain wikilinks mixed with prose.

    Prose between links is HTML-escaped; links become markup. Doing both in one
    pass is what keeps a value like `"[[A]] & [[B]]"` from either double-escaping
    the rendered anchors or leaving the `&` raw.
    """
    parts: list[str] = []
    resolutions: list[Resolution] = []
    position = 0

    for match, ref in iter_refs(text):
        parts.append(html.escape(text[position : match.start()]))
        resolution = resolve(ref, ctx)
        ref.resolution = resolution
        resolutions.append(resolution)
        parts.append(render(ref, resolution))
        position = match.end()

    parts.append(html.escape(text[position:]))
    return "".join(parts), resolutions


def render(ref: WikiRef, resolution: Resolution) -> str:
    """Render a resolved reference as inline HTML.

    HTML rather than markdown link syntax because the class names are the
    point: they are what let the vendored Obsidian theme style these.
    """
    label = html.escape(resolution.label)

    if ref.is_embed and resolution.kind is LinkKind.ASSET:
        return f'<img src="{html.escape(resolution.url or "")}" alt="{label}" />'

    if resolution.kind in (LinkKind.PUBLISHED, LinkKind.ASSET):
        # A non-embed asset reference is a link *to* the image, which is how
        # Obsidian renders `[[picture.png]]` without the leading `!`.
        return f'<a class="internal-link" href="{html.escape(resolution.url or "")}">{label}</a>'

    if resolution.kind is LinkKind.EXTERNAL:
        return (
            f'<a class="external-link" href="{html.escape(resolution.url or "")}" '
            f'rel="noopener">{label}</a>'
        )

    if resolution.kind is LinkKind.UNPUBLISHED:
        # A real note the reader cannot reach yet. Not a broken link -- it will
        # become one the moment its `publish` flag flips, with no reparse of
        # the linking note needed beyond a normal run. The tooltip says so,
        # rather than leaving the reader to wonder why the text is inert.
        return (
            f'<span class="internal-link is-unpublished" title="{UNPUBLISHED_TITLE}">'
            f"{label}</span>"
        )

    # MISSING / ASSET_MISSING: Obsidian's own class for a link with no target.
    return f'<span class="internal-link is-unresolved">{label}</span>'
