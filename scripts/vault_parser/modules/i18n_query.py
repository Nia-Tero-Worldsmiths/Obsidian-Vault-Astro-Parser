"""The ```i18n-query fence: a table of other notes, chosen by the shared table.

The native replacement for the ```dataview tables on published notes. Three
things it does that the queries it replaces could not:

* **Column headers and row names translate.** A `ja` reader of a party page
  got "Portrait" and "Nombre" regardless; now the headers come from the shared
  table's `labels:` and each row's name resolves through `title_<lang>`.
* **One portrait, chosen by the note.** `defaultImageIndex` decides which
  image stands for a member, instead of whatever `imagen` happened to hold
  first -- which is why one query in the vault had `imagen[0]` written into it
  by hand the day a note gained a second image.
* **Membership by tag *or* by a frontmatter key.** Briar belongs to Custodios
  de Juutei through `organizacion` and carries no tags at all, so the tag query
  could not see her.

Everything that differs between one use of this and another is layout data in
the shared table, never code here. A roster selects by tag or `organizacion`
and sorts by title; a chapter list would select by `padre` and sort by numeric
filename -- the same module, a different `groups:` entry, which is why this is
named for what it does rather than for the first thing it was used on.

The fence is usually empty: the layout comes from the note's own NoteType,
exactly as `i18n-infobox` does, and the filter term defaults to the note's own
name. Only a table whose tag is a *role* rather than the thing's name needs to
say anything:

    ```i18n-query
    from: inquisidor
    not: ex-inquisidor
    ```

Markup is emitted through `grouping.render_table`, the same function
`dataview_queries` uses, so a note switching over changes what it says and
never how it looks -- `Cards.css` keys off that exact DOM.
"""

from __future__ import annotations

import html
import re
from typing import Any

from .. import grouping, infobox as ib, langblocks, linking
from ..model import Note, VaultContext
from .base import TransformModule

_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*i18n-query[ \t]*\n"
    r"(?P<body>.*?)"
    r"^(?P=indent)(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

FENCE_NAME = "i18n-query"


class I18nQueryModule(TransformModule):
    name = "i18n_query"
    # Immediately after `dataview_queries` (60): the same job in the new idiom,
    # and before `image_licensing` (82), so the portraits this emits are
    # licensed like any other image.
    order = 62
    summary = "Render ```i18n-query fences as a table of related notes"
    stub = False

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        self._table: ib.InfoboxTable | None = None
        #: vault_path -> NoteType, for types the table declares no group for.
        self._unknown_types: dict[str, str] = {}
        #: Notes whose fence sits where it renders in one language only.
        self._misplaced: set[str] = set()
        #: vault_path -> term, for a filter that matched nothing at all.
        self._empty: dict[str, str] = {}
        #: Notes already checked for placement, which is a per-note question
        #: while `transform` runs once per language body.
        self._placement_checked: set[str] = set()

    # -- per note --------------------------------------------------------

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        if FENCE_NAME not in note.body:
            return note

        self._check_placement(note)

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

    # -- placement -------------------------------------------------------

    def _check_placement(self, note: Note) -> None:
        """Warn when the fence renders in the default language only.

        A fence below the first `:::lang` marker but outside every block is
        default-language content, and looks shared in the editor -- the one
        placement that can surprise an author. Read off `raw_body`, which is
        never mutated, so this does not depend on module order.

        Deliberately not the exception `langblocks` makes for an unwrapped
        `i18n-infobox`, which is forced shared wherever it sits. An infobox is
        one per note and re-renders itself; this table may legitimately differ
        per language, so overriding the author's placement would take away the
        control that placement is *for*.
        """
        if note.vault_path in self._placement_checked:
            return
        self._placement_checked.add(note.vault_path)

        blocks = langblocks.parse_blocks(note.raw_body)
        if not blocks:
            return

        default_only = langblocks.default_only_lines(note.raw_body, blocks)
        for match in _FENCE.finditer(note.raw_body):
            if note.raw_body.count("\n", 0, match.start()) in default_only:
                self._misplaced.add(note.vault_path)
                self.count("fences placed outside every block")

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

        type_key = options.get("layout") or note.frontmatter.get("NoteType")
        layout = table.group_for(type_key)
        if layout is None:
            shown = str(type_key).strip() if type_key else "—"
            self._unknown_types[note.vault_path] = shown
            return _warning(
                f"no query layout for NoteType «{shown}»; "
                "add one under `groups:` in the translation table"
            )

        written = options.get("from") or ""
        term = grouping.normalize_term(written or _basename(note))
        excluded = grouping.normalize_term(options.get("not") or "")

        # The thing the table is about, for comparing membership links by
        # identity. With no `from:` that is the note the fence sits on, which is
        # the common case and the one that makes aliases work for free.
        target = ctx.find_note(written) if written else note

        rows = self._select(note, ctx, layout, term, excluded, target)
        if not rows:
            self._empty[note.vault_path] = term

        self.count("query tables rendered")
        self.count("result rows", len(rows))

        default = ctx.config.i18n.default
        sections = grouping.partition(rows, layout.get("sections"))
        return "\n" + "".join(
            self._table_html(layout, members, caption, ctx, table, lang, default)
            for caption, members in sections
        ) + "\n"

    def _select(
        self,
        note: Note,
        ctx: VaultContext,
        layout: dict[str, Any],
        term: str,
        excluded: str,
        target: Note | None,
    ) -> list[Note]:
        """Everyone the term names, by tag or by the layout's own key."""
        by_tag, field = grouping.membership(layout)

        members = [
            other
            for other in ctx.notes
            if other is not note
            and (
                (by_tag and grouping.has_tag(other, term))
                or (field and grouping.field_links_to(other, field, term, ctx, target))
            )
            and not (excluded and grouping.has_tag(other, excluded))
        ]
        grouping.sort_members(members, layout.get("sort"))
        return members

    def _table_html(
        self,
        layout: dict[str, Any],
        members: list[Note],
        caption_key: str,
        ctx: VaultContext,
        table: ib.InfoboxTable,
        lang: str,
        default: str,
    ) -> str:
        columns = [c for c in layout.get("columns") or [] if isinstance(c, dict)]

        headers = [
            ib.resolve_label(table, str(column.get("label") or ""), lang, default)
            for column in columns
        ]
        rows = [
            [self._cell(column, member, ctx, lang, default) for column in columns]
            for member in members
        ]
        caption = (
            ib.resolve_label(table, caption_key, lang, default) if caption_key else None
        )
        return grouping.render_table(headers, rows, caption)

    def _cell(
        self,
        column: dict[str, Any],
        member: Note,
        ctx: VaultContext,
        lang: str,
        default: str,
    ) -> str:
        kind = str(column.get("kind") or "link")
        # The member's own name in the reader's language. Without this a `ja`
        # reader would get the whole table in Spanish, which is most of what
        # this fence exists to fix. The link target is untouched: one note, one
        # URL, whatever it is called here.
        name = ib.resolve_title({}, member.frontmatter, lang, default, _basename(member))

        if kind == "image":
            return grouping.render_portrait(
                str(column.get("key") or "imagen"),
                member,
                ctx,
                grouping.parse_size(column.get("size")),
                count=self.count,
                warn=self.warn,
                alt=name,
            )

        if kind == "link":
            return linking.render_note(member, label=name)

        value = member.frontmatter.get(str(column.get("key") or ""))
        if value is None:
            return grouping.NULL_TEXT
        if isinstance(value, list):
            parts = [
                linking.render_text_with_links(str(item).strip(), ctx)[0]
                for item in value
                if item is not None
            ]
            return ", ".join(p for p in parts if p) or grouping.NULL_TEXT
        return linking.render_text_with_links(str(value).strip(), ctx)[0]

    # -- reporting -------------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        self._report_misplaced()
        self._report_unknown_types()
        self._report_empty()

    def _report_misplaced(self) -> None:
        if not self._misplaced:
            return
        self.section(
            "Query table: renders in one language only",
            [
                "These fences sit below the first `:::lang` marker but outside",
                "every block, so they are default-language content while looking",
                "shared. Move one into the note's preamble, or wrap it in",
                "`:::lang all`, to show it in every language.",
                "",
                *(f"  {path}" for path in sorted(self._misplaced)[:15]),
            ],
        )

    def _report_unknown_types(self) -> None:
        if not self._unknown_types:
            return
        lines = [
            "These notes declare a query table but the shared table has no",
            "`groups:` entry for their NoteType, so a warning was rendered in",
            "place.",
            "",
        ]
        for path, shown in sorted(self._unknown_types.items())[:15]:
            lines.append(f"  {path}")
            lines.append(f"      NoteType: {shown}")
        self.section("Query table: unknown NoteType", lines)

    def _report_empty(self) -> None:
        """A table that found nobody.

        Rendered as an empty table rather than hidden -- the heading above it
        would otherwise introduce nothing -- but worth reporting, because the
        usual cause is a filter term matching neither a tag nor the layout's
        membership key, which looks identical to a thing with no members yet.
        """
        if not self._empty:
            return
        lines = [
            "These query tables matched no notes. Either nothing belongs to them",
            "yet, or the term matches neither a tag nor the layout's membership",
            "key.",
            "",
        ]
        for path, term in sorted(self._empty.items())[:15]:
            lines.append(f"  {path}")
            lines.append(f"      looked for: {term or '(empty)'}")
        self.section("Query table: no rows matched", lines)


def _basename(note: Note) -> str:
    """The note's filename without its suffix, which is what a wikilink names."""
    return note.vault_path.rsplit("/", 1)[-1].removesuffix(".md")


def _parse_options(source: str) -> dict[str, str] | str:
    """The fence body: a tiny `key: value` mapping, or an error to show.

    Parsed by hand rather than with YAML, because the whole grammar is three
    optional keys and a hand-rolled reader gives a better message than a YAML
    traceback for the one mistake an author is likely to make.
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


_KEYS = frozenset({"from", "not", "layout"})


def _warning(message: str) -> str:
    return (
        f'\n<div class="ml-infobox-warning">'
        f'<span class="ml-infobox-warning-prefix">{FENCE_NAME}: </span>'
        f"<span>{html.escape(message)}</span></div>\n"
    )
