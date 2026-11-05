"""Fenced dataview queries.

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

from .. import dataview, grouping, linking
from ..dataview import Column, Query, UnsupportedQuery
from ..grouping import NULL_TEXT
from ..model import Note, VaultContext
from .base import TransformModule

_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*dataview[ \t]*\n"
    r"(?P<body>.*?)"
    r"^(?P=indent)(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)


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
            candidates = [n for n in candidates if grouping.matches_tags(n, query.tags)]
        if query.where is not None:
            candidates = [
                n for n in candidates if grouping.matches_where(n, query.where, note, ctx)
            ]

        if query.sort is not None:
            candidates.sort(
                key=lambda n: grouping.sort_key(n, query.sort),
                reverse=query.sort.descending,
            )
        else:
            candidates.sort(key=grouping.title_sort_key)

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

        return grouping.render_table(
            [column.header for column in columns],
            [
                [self._render_cell(column, note, ctx) for column in columns]
                for note in rows
            ],
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
        return grouping.render_portrait(
            field, note, ctx, size, count=self.count, warn=self.warn
        )
