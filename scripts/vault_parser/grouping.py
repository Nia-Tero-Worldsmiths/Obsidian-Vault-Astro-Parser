"""Choosing a set of notes and rendering them as a table.

Shared by `dataview_queries`, which runs the vault's existing ```dataview
fences, and `i18n_query`, the native replacement. The two have to emit *identical*
markup: a note switching from one to the other should change what it says, never
how it looks, and the vault's `Cards.css` keys off this exact DOM.

Everything here is a free function taking its reporting channels explicitly,
rather than methods on a module, precisely so both modules can call it without
one importing the other.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Callable, Protocol

from . import dataview, infobox as ib, linking
from .model import Note, VaultContext, index_key
from .slugs import natural_key


class Counter(Protocol):
    def __call__(self, name: str, amount: int = 1) -> None: ...


Warner = Callable[[str], None]

#: Shown for a cell the note has no value for. Escaped, because a bare "-" at
#: the start of a table cell would otherwise start a markdown list.
NULL_TEXT = r"\-"

_SEPARATORS = re.compile(r"[\s_\-‐-―]+")


def normalize_term(raw: Any) -> str:
    """Fold a filter term, a tag or an organisation name to one comparable key.

    Deliberately *not* `infobox.normalize_type_key`, which this builds on. That
    one folds case and strips diacritics, and is used to match a NoteType
    against a layout name; widening it to drop separators as well would quietly
    change which layouts match, which nothing asked for.

    This additionally drops spaces, underscores and every flavour of dash,
    because the vault spells the same membership three different ways:

        #blackShawarma       [[Black Shawarma]]        -> blackshawarma
        #corazasPetreas      [[Corazas Pétreas]]       -> corazaspetreas
        #custodios-de-juutei [[Custodios de Juutei]]   -> custodiosdejuutei

    Folding all of them is what lets one written term match both a tag and an
    `organizacion` value without the author restating either.
    """
    folded = ib.normalize_type_key(raw)
    return _SEPARATORS.sub("", folded)


# -- selection ------------------------------------------------------------


def matches_tags(note: Note, tags: dataview.TagFilter) -> bool:
    if not tags.terms:
        return False

    present = {tag.casefold() for tag in note.tags}
    results = [
        (tag.casefold() not in present) if negated else (tag.casefold() in present)
        for negated, tag in tags.terms
    ]
    return any(results) if tags.operator == "OR" else all(results)


def matches_where(
    note: Note, condition: dataview.Condition, current: Note, ctx: VaultContext
) -> bool:
    value = note.frontmatter.get(condition.field)

    if condition.kind == "truthy":
        return bool(value) and value not in ("", [], {})

    # `field = this.file.link`. Resolving both sides to notes and comparing
    # identity, rather than comparing strings: the link text is a *filename*
    # while the note may carry a different `title`, and aliases and full-path
    # links have to match too.
    for candidate in value if isinstance(value, list) else [value]:
        if not isinstance(candidate, str):
            continue
        for ref in linking.parse_refs(candidate):
            if ctx.find_note(ref.target) is current:
                return True
        if index_key(candidate) == index_key(current.vault_path.rsplit("/", 1)[-1]):
            return True
    return False


def has_tag(note: Note, term: str) -> bool:
    """Whether any of the note's tags folds to `term`."""
    if not term:
        return False
    return any(normalize_term(tag) == term for tag in note.tags)


def field_links_to(
    note: Note,
    field: str,
    term: str,
    ctx: VaultContext,
    target: Note | None = None,
) -> bool:
    """Whether `field` names the organisation the caller is asking about.

    Two comparisons, and both are needed. `target` is the organisation's own
    note when there is one, and a link is resolved and compared by *identity*
    against it, so an alias or a full-path link still matches. The folded
    string comparison is the fallback for a value naming something that has no
    note of its own.

    `target` is passed in rather than looked up from `term`, because `term` has
    already been folded for comparison and `ctx.find_note` only casefolds --
    `find_note("custodiosdejuutei")` can never resolve a note called "Custodios
    de Juutei", so looking it up here would silently disable the identity half.
    """
    value = note.frontmatter.get(field)

    for candidate in value if isinstance(value, list) else [value]:
        if not isinstance(candidate, str):
            continue
        for ref in linking.parse_refs(candidate):
            if target is not None and ctx.find_note(ref.target) is target:
                return True
            if normalize_term(ref.target) == term:
                return True
        if normalize_term(candidate) == term:
            return True
    return False


# -- sorting --------------------------------------------------------------

# For `SORT number(field)`: dataview's own NUMBER_REGEX, which is unanchored,
# so it picks up the first number anywhere in the value.
_DIGITS = re.compile(r"(-?\d+(?:\.\d+)?)")


def field_value(note: Note, field_name: str) -> str:
    """Resolve a sortable field, including dataview's `file.*` pseudo-fields."""
    if field_name in ("file.name", "file.link"):
        # The *filename*, which is not the title -- a note can override one
        # without the other, and `file.name` means the file.
        return note.vault_path.rsplit("/", 1)[-1].removesuffix(".md")
    if field_name == "file.path":
        return note.vault_path
    if field_name == "title":
        return note.title
    value = note.frontmatter.get(field_name)
    if value is None:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value)


def sort_key(note: Note, sort: dataview.SortKey) -> tuple:
    raw = field_value(note, sort.field)

    if sort.numeric:
        match = _DIGITS.search(raw)
        # Nulls last, matching dataview's own ordering for missing values.
        return (0, float(match.group(1))) if match else (1, float("inf"))

    if not raw:
        return (1, ())
    return (0, natural_key(raw.casefold()))


def title_sort_key(note: Note) -> tuple:
    """The default order: by what the reader sees, naturally sorted."""
    return natural_key(note.title.casefold())


# -- layout-driven selection ----------------------------------------------


def membership(layout: dict[str, Any]) -> tuple[bool, str]:
    """(match by tag?, frontmatter key that names the subject) for a layout.

    The one axis on which two uses of a query table genuinely differ. A roster
    takes `{tag: true, field: organizacion}`; a chapter list would take
    `{field: padre}` and no tag half. Everything else -- columns, sort,
    sections -- is already data, which is why both are one module.

    Defaults to tag-only: a layout that says nothing gets the simplest
    behaviour rather than silently inheriting a key from the first use case
    this was written for.
    """
    spec = layout.get("members")
    if not isinstance(spec, dict):
        return True, ""
    field = spec.get("field")
    return bool(spec.get("tag", True)), str(field).strip() if field else ""


def sort_members(members: list[Note], spec: Any) -> None:
    """Order rows in place, by `sort: { by: ..., numeric: ..., desc: ... }`.

    `by` accepts the same field names `field_value` does, including dataview's
    `file.name` -- which is what a chapter list sorts on, since a chapter's
    title and its filename are not the same string.
    """
    if not isinstance(spec, dict):
        members.sort(key=title_sort_key)
        return

    field = str(spec.get("by") or "title")
    numeric = bool(spec.get("numeric", False))
    members.sort(
        key=lambda note: _spec_key(note, field, numeric),
        reverse=bool(spec.get("desc", False)),
    )


def _spec_key(note: Note, field: str, numeric: bool) -> tuple:
    raw = field_value(note, field)
    if numeric:
        match = _DIGITS.search(raw)
        # Nulls last, matching dataview's own ordering for missing values.
        return (0, float(match.group(1))) if match else (1, float("inf"))
    if not raw:
        return (1, ())
    return (0, natural_key(raw.casefold()))


def partition(members: list[Note], spec: Any) -> list[tuple[str, list[Note]]]:
    """Split rows into labelled sections, as `sections:` declares them.

    Each section may name a `tag`; a row lands in the *first* section whose tag
    it carries, so the order in the table is the order on the page. A section
    with no tag is the catch-all, and one is prepended when the layout declares
    none -- otherwise a row carrying no section tag would simply vanish, which
    is the one failure a reader could never notice.

    No `sections:` at all gives one unlabelled table, which is what every
    layout did before sections existed. Empty sections are dropped rather than
    drawn as a caption introducing nothing.
    """
    specs = [s for s in spec or [] if isinstance(s, dict)] if isinstance(spec, list) else []
    if not specs:
        return [("", members)]

    if all(section.get("tag") for section in specs):
        specs = [{}, *specs]

    buckets: list[tuple[dict[str, Any], list[Note]]] = [(s, []) for s in specs]
    catch_all = next(bucket for section, bucket in buckets if not section.get("tag"))

    for member in members:
        for section, bucket in buckets:
            tag = normalize_term(section.get("tag"))
            if tag and has_tag(member, tag):
                bucket.append(member)
                break
        else:
            catch_all.append(member)

    return [
        (str(section.get("label") or ""), bucket)
        for section, bucket in buckets
        if bucket
    ]


# -- rendering ------------------------------------------------------------


_SIZE = re.compile(r"^\s*(\d+)\s*(?:x\s*(\d+)\s*)?$", re.IGNORECASE)


def parse_size(raw: Any) -> tuple[int, int | None] | None:
    """`"500x500"` or `"250"` into the pair the portrait renderer wants."""
    if raw is None:
        return None
    match = _SIZE.match(str(raw))
    if not match:
        return None
    width, height = match.groups()
    return int(width), int(height) if height else None


def render_portrait(
    field: str,
    note: Note,
    ctx: VaultContext,
    size: tuple[int, int | None] | None,
    *,
    count: Counter,
    warn: Warner,
    alt: str | None = None,
) -> str:
    """One image standing for `note`, or "" when there is nothing to show.

    `alt` overrides the subject's name, for a caller that knows the reader's
    language -- a roster in Japanese should not describe its pictures in
    Spanish. Defaults to `note.title`, which is what the dataview fences have
    always used.
    """
    # A gallery note holds a list here; a roster row wants one portrait,
    # and which one is the note's decision -- `defaultImageIndex`.
    target = ib.default_image_ref(note.frontmatter, field)
    if not target:
        # A roster row for someone with no portrait. The stand-in is keyed
        # on the *subject's* NoteType, not the page the table sits on --
        # this is a list of people whichever note is showing it.
        target = ctx.config.fallback_images.get(
            ib.normalize_type_key(note.frontmatter.get("NoteType"))
        )
        if not target:
            count("portrait missing")
            return ""
        count("portraits filled from fallback_images")
    refs = linking.parse_refs(target)
    if refs:
        target = refs[0].target

    resolved = linking.resolve_image(target, ctx)
    if resolved is None:
        count("portrait unresolved")
        warn(f"{note.vault_path}: image not found for `{field}`: {target}")
        return ""

    url, asset = resolved
    if asset is not None:
        # Always copied, even for an unpublished note: an image has no
        # frontmatter and so no publish gate of its own. An icon needs no such
        # record -- it is never copied.
        ctx.referenced_assets.add(asset.vault_path)
    count("portraits rendered")

    # `embed(link(imagen, "500x500"))` -- Obsidian reads an embed's display
    # text as dimensions, exactly as `![[img.jpg|500x500]]` does, and emits
    # them as width/height *attributes*. That matters: attributes are
    # presentational hints, so Cards.css can still override the width to
    # fill its grid column while the height keeps every card aligned.
    # Inline styles would win over Cards.css and break that.
    # The height goes in an inline style, not just the attribute: with
    # Cards.css setting `width: 100%`, the browser sizes the image from its
    # natural ratio and the height attribute never takes effect. Only the
    # *height* is inlined -- inlining the width too would beat Cards.css and
    # stop the card filling its grid column. Cards.css then caps it via
    # `max-height: var(--cards-image-height)`, which is what produces the
    # uniform 400px card images Obsidian shows.
    dimensions = ""
    if size is not None:
        width, height = size
        dimensions = f' width="{width}"'
        if height is not None:
            dimensions += f' height="{height}" style="height: {height}px"'
        count("portraits sized")

    described = alt if alt is not None else note.title
    return f'<img src="{html.escape(url)}" alt="{html.escape(described)}"{dimensions} />'


def render_table(
    headers: list[str],
    rows: list[list[str]],
    caption: str | None = None,
) -> str:
    """The markup both fences emit, down to the class names.

    `headers` is plain text and is escaped here; each cell in `rows` is already
    rendered HTML. A `caption` is a native table child, so it sits outside the
    `tbody` that `Cards.css` turns into a grid -- which is why a section label
    is a caption and not a header row that would render as one more card.
    """
    head = "".join(
        f'<th class="table-view-th">{html.escape(text)}</th>' for text in headers
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    legend = (
        f'<caption class="table-view-caption">{html.escape(caption)}</caption>'
        if caption
        else ""
    )
    return (
        '<table class="dataview table-view-table">'
        f"{legend}"
        f'<thead class="table-view-thead"><tr class="table-view-tr-header">{head}</tr></thead>'
        f'<tbody class="table-view-tbody">{body}</tbody>'
        "</table>"
    )
