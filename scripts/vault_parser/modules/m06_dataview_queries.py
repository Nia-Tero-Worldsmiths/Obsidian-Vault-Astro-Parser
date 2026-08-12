"""Module 6 -- Fenced dataview queries.

Runs each ```dataview block against the note index **at parse time** and emits
static markup. No dataview, and no query engine of any kind, reaches the
browser.

Rendered DOM matches Obsidian's (`table.dataview.table-view-table`,
`ul.dataview.list-view-ul`), because the vault's `Cards.css` snippet keys off
exactly that -- `.cards table.dataview tbody > tr > td:has(img)` is what turns
these tables into portrait grids for notes carrying `cssclasses: cards`.

Publish policy for result rows, which differs by column type on purpose:

* **Names** follow the site-wide link rule -- published notes link, unpublished
  ones render as `.is-unpublished` plain text.
* **Portraits always render.** Images have no frontmatter and therefore no
  publish gate of their own; the author's equivalent of unpublishing an image
  is not putting it in the vault.

An unsupported query is left as its original fence and reported, so it shows
up as an obvious to-do rather than an empty table.
"""

from __future__ import annotations

import html
import re

from .. import dataview, linking
from ..dataview import Column, Query, UnsupportedQuery
from ..model import Note, VaultContext, index_key
from ..slugs import natural_key
from .base import TransformModule

_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*dataview[ \t]*\n"
    r"(?P<body>.*?)"
    r"^(?P=indent)(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

NULL_TEXT = r"\-"


class DataviewQueriesModule(TransformModule):
    name = "dataview_queries"
    order = 60
    summary = "Execute fenced dataview queries at parse time into static markup"
    stub = False

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if "dataview" not in note.body:
            return note

        def replace(match: re.Match) -> str:
            source = match.group("body")
            try:
                query = dataview.parse(source)
            except UnsupportedQuery as exc:
                self.count("unsupported")
                self.warn(f"{note.vault_path}: {exc}")
                return match.group(0)

            for warning in query.warnings:
                self.warn(f"{note.vault_path}: {warning}")

            rows = self._evaluate(query, note, ctx)
            self.count(f"{query.form} query")
            self.count("result rows", len(rows))
            return self._render(query, rows, ctx)

        note.body = _FENCE.sub(replace, note.body)
        return note

    # -- evaluation ------------------------------------------------------

    def _evaluate(self, query: Query, note: Note, ctx: VaultContext) -> list[Note]:
        candidates = [other for other in ctx.notes if other is not note]

        if query.tags is not None:
            candidates = [n for n in candidates if _matches_tags(n, query.tags)]
        if query.where is not None:
            candidates = [n for n in candidates if _matches_where(n, query.where, note, ctx)]

        if query.sort is not None:
            candidates.sort(
                key=lambda n: _sort_key(n, query.sort),
                reverse=query.sort.descending,
            )
        else:
            candidates.sort(key=lambda n: natural_key(n.title.casefold()))

        return candidates

    # -- rendering -------------------------------------------------------

    def _render(self, query: Query, rows: list[Note], ctx: VaultContext) -> str:
        body = self._render_list(rows) if query.form == "list" else self._render_table(
            query, rows, ctx
        )
        # Blank lines so the surrounding markdown does not absorb the block.
        return f"\n{body}\n"

    def _render_list(self, rows: list[Note]) -> str:
        if not rows:
            return '<ul class="dataview list-view-ul"></ul>'
        items = "".join(
            f'<li class="dataview-result-list-li">{linking.render_note(note)}</li>'
            for note in rows
        )
        return f'<ul class="dataview list-view-ul">{items}</ul>'

    def _render_table(self, query: Query, rows: list[Note], ctx: VaultContext) -> str:
        columns = list(query.columns)
        if not query.without_id:
            columns.insert(0, Column("file_link", None, "File"))

        headers = "".join(
            f'<th class="table-view-th">{html.escape(column.header)}</th>'
            for column in columns
        )
        body_rows = "".join(
            "<tr>"
            + "".join(
                f"<td>{self._render_cell(column, note, ctx)}</td>" for column in columns
            )
            + "</tr>"
            for note in rows
        )
        return (
            '<table class="dataview table-view-table">'
            f'<thead class="table-view-thead"><tr class="table-view-tr-header">{headers}</tr></thead>'
            f'<tbody class="table-view-tbody">{body_rows}</tbody>'
            "</table>"
        )

    def _render_cell(self, column: Column, note: Note, ctx: VaultContext) -> str:
        if column.kind == "file_link":
            return linking.render_note(note)

        if column.kind == "embed":
            return self._render_portrait(column.field or "", note, ctx, column.size)

        value = note.frontmatter.get(column.field or "")
        if column.field == "title":
            value = note.title
        if value is None:
            return NULL_TEXT
        if isinstance(value, list):
            parts = [self._render_scalar(item, ctx) for item in value if item is not None]
            return ", ".join(p for p in parts if p) or NULL_TEXT
        return self._render_scalar(value, ctx)

    def _render_scalar(self, value, ctx: VaultContext) -> str:
        if not isinstance(value, str):
            return html.escape(str(value))
        rendered, _ = linking.render_text_with_links(value.strip(), ctx)
        return rendered

    def _render_portrait(
        self,
        field: str,
        note: Note,
        ctx: VaultContext,
        size: tuple[int, int | None] | None = None,
    ) -> str:
        raw = note.frontmatter.get(field)
        if not isinstance(raw, str) or not raw.strip():
            self.count("portrait missing")
            return ""

        target = raw.strip()
        refs = linking.parse_refs(target)
        if refs:
            target = refs[0].target

        resolved = linking.resolve_image(target, ctx)
        if resolved is None:
            self.count("portrait unresolved")
            self.warn(f"{note.vault_path}: image not found for `{field}`: {target}")
            return ""

        url, asset = resolved
        if asset is not None:
            # Always copied, even for an unpublished note -- see the module
            # docstring. An icon needs no such record -- it is never copied.
            ctx.referenced_assets.add(asset.vault_path)
        self.count("portraits rendered")

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
            self.count("portraits sized")

        return (
            f'<img src="{html.escape(url)}" alt="{html.escape(note.title)}"'
            f"{dimensions} />"
        )


# -- predicates -----------------------------------------------------------


def _matches_tags(note: Note, tags: dataview.TagFilter) -> bool:
    if not tags.terms:
        return False

    present = {tag.casefold() for tag in note.tags}
    results = [
        (tag.casefold() not in present) if negated else (tag.casefold() in present)
        for negated, tag in tags.terms
    ]
    return any(results) if tags.operator == "OR" else all(results)


def _matches_where(
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


# For `SORT number(field)`: dataview's own NUMBER_REGEX, which is unanchored,
# so it picks up the first number anywhere in the value.
_DIGITS = re.compile(r"(-?\d+(?:\.\d+)?)")


def _field_value(note: Note, field_name: str) -> str:
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


def _sort_key(note: Note, sort: dataview.SortKey) -> tuple:
    raw = _field_value(note, sort.field)

    if sort.numeric:
        match = _DIGITS.search(raw)
        # Nulls last, matching dataview's own ordering for missing values.
        return (0, float(match.group(1))) if match else (1, float("inf"))

    if not raw:
        return (1, ())
    return (0, natural_key(raw.casefold()))
