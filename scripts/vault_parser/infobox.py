"""Resolving the plugin-rendered infobox.

A port of the i18n Manager plugin's `src/infobox/model.ts` and
`src/infobox/table.ts`. Those are deliberately free of any Obsidian import so
they can be unit-tested on their own, which is what makes this a mechanical
translation rather than reverse engineering. Keep it that way: nothing here
touches the pipeline, so it stays testable in isolation.

Every function is total. A malformed or missing table must degrade to
something readable, never raise -- the alternative is a note whose whole
infobox disappears because one row was mistyped.

**One deliberate divergence.** The plugin reads Obsidian's raw frontmatter,
where `key_ja: ""` blanks a row while `key_ja:` (null) falls back to `key`.
The parser reads frontmatter that `frontmatter._clean_value` has already
normalised, and that maps `""` -- along with `-`, `[[]]` and `![[]]` -- to
None. So in the parser both spellings fall back to `key`. This is the better
behaviour of the two (an empty string is how a half-filled template reads), but
it is a difference, and it is the one place a note could render differently
here than in Obsidian.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import vault
from .config import Config
from .frontmatter import normalize_flow_sequences

#: Shown in place of a value the note does not define, unless the table says
#: otherwise. Matches the legacy dataview infobox, where an unset `=this.x`
#: rendered as a dash.
DEFAULT_PLACEHOLDER = "-"

#: Info string of the fence that declares an infobox.
INFOBOX_FENCE = "i18n-infobox"

#: Sentinel meaning "every language at once"; renders exactly as the default.
ALL = "ALL"

_COMBINING = re.compile(r"[̀-ͯ]")

# A fenced yaml block, so the table may live in a `.md` note instead of a bare
# `.yaml` file. First fence wins; `yaml` or `yml`, any case, three or more
# backticks.
_YAML_FENCE = re.compile(
    r"^[ \t]*`{3,}[ \t]*ya?ml[ \t]*\r?\n(.*?)^[ \t]*`{3,}[ \t]*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def normalize_type_key(raw: Any) -> str:
    """Fold a NoteType down to a layout key.

    The vault is not consistent: every template uses a lowercase unaccented
    slug (`lugar`, `persona`) except Misión, which writes `NoteType: Misión`.
    Rather than require that fixed across 248 notes, fold case and strip
    diacritics on both sides of the comparison.
    """
    if not isinstance(raw, str):
        return ""
    folded = unicodedata.normalize("NFD", raw.strip().lower())
    return _COMBINING.sub("", folded)


@dataclass(frozen=True)
class RowSpec:
    """A layout row after its optional marker has been stripped off."""

    key: str
    optional: bool


def parse_row_spec(entry: str) -> RowSpec:
    """Split a row entry into its key and its optionality.

    The marker is a trailing `?` rather than a mapping, so a layout stays a
    readable one-line list: `[especie, genero, creencia?, titulos?]`. Trim
    first -- a quoted entry with stray whitespace would otherwise keep its `?`
    and be looked up as a key that does not exist.
    """
    text = str(entry).strip()
    optional = text.endswith("?")
    return RowSpec(key=(text[:-1] if optional else text).strip(), optional=optional)


@dataclass(frozen=True)
class ResolvedRow:
    key: str
    label: str
    #: Markdown; wikilinks are rendered by the caller.
    values: list[str]
    prefix: str = ""
    suffix: str = ""
    #: True when the note defines nothing and `values` holds the placeholder.
    missing: bool = False
    #: Set when the row is structurally wrong rather than merely empty --
    #: currently only a grouped child with no parent at its position.
    problem: str = ""


@dataclass(frozen=True)
class ResolvedSection:
    heading: str
    rows: list[ResolvedRow]


@dataclass(frozen=True)
class ResolvedStatus:
    symbol: str
    tooltip: str
    #: True when the note's value matched nothing and the fallback was used.
    #: Distinct from a deliberate "unknown": that is a fact about the
    #: character, this is a typo in the note.
    fallback: bool


@dataclass
class InfoboxTable:
    """The parsed translation table, plus whatever went wrong reading it."""

    placeholder: str = DEFAULT_PLACEHOLDER
    status: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: dict[str, dict[str, Any]] = field(default_factory=dict)
    values: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    layouts: dict[str, dict[str, Any]] = field(default_factory=dict)
    available: bool = False
    error: str = ""

    def layout_for(self, note_type: Any) -> dict[str, Any] | None:
        """Look up a layout, tolerating case and accent differences."""
        wanted = normalize_type_key(note_type)
        if not wanted:
            return None
        for key, layout in self.layouts.items():
            if normalize_type_key(key) == wanted:
                return layout if isinstance(layout, dict) else None
        return None


# --- reading the table ------------------------------------------------------


def sanitize_yaml(text: str) -> str:
    """Make the table parseable by PyYAML without editing the vault.

    The layouts use two spellings Obsidian's YAML parser accepts and PyYAML
    rejects -- the optional-row marker (`[clase, subclase?]`) and positional
    holes (`[, Tierra]`). The real vault table hits the first of these, and a
    failure here is not local: PyYAML refuses the whole document, so one
    mistyped row would take every infobox in the vault down with it.

    Shared with note frontmatter, which needs exactly the same treatment --
    see `frontmatter.normalize_flow_sequences` for the full reasoning.
    """
    return normalize_flow_sequences(text)


def extract_yaml_body(raw: str) -> str:
    match = _YAML_FENCE.search(raw)
    return match.group(1) if match else raw


def load_table(config: Config) -> InfoboxTable:
    """Read the shared translation table out of the vault.

    The table lives under `z_Templates`, which the note walk ignores, so it is
    read explicitly through the read-only layer -- the same way `graph` reads
    `.obsidian/graph.json`.
    """
    relative = (config.i18n.infobox_table or "").strip()
    if not relative:
        return InfoboxTable(error="`i18n.infobox_table` is empty; no infobox was rendered")

    path = config.vault_root / relative
    if not path.exists():
        return InfoboxTable(error=f"{relative} not found; no infobox was rendered")

    try:
        text = sanitize_yaml(extract_yaml_body(vault.read_text(path, config.vault_root)))
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        brief = " ".join(str(exc).split())[:100]
        return InfoboxTable(error=f"{relative} is not valid YAML ({brief})")

    if not isinstance(raw, dict):
        return InfoboxTable(error=f"{relative} must contain a YAML mapping")

    layouts = raw.get("layouts") or {}
    if not isinstance(layouts, dict) or not layouts:
        return InfoboxTable(error=f"{relative} declares no `layouts`")

    placeholder = raw.get("placeholder", DEFAULT_PLACEHOLDER)
    return InfoboxTable(
        placeholder=str(placeholder) if placeholder is not None else DEFAULT_PLACEHOLDER,
        status=_mapping(raw.get("status")),
        labels=_mapping(raw.get("labels")),
        values=_mapping(raw.get("values")),
        layouts=layouts,
        available=True,
    )


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


# --- labels and decorations -------------------------------------------------


def _is_default(lang: str, default: str) -> bool:
    return lang == ALL or lang.casefold() == default.casefold()


def resolve_label(table: InfoboxTable, key: str, lang: str, default: str) -> str:
    """`labels[key][lang]` -> `labels[key][default]` -> the raw key.

    Falling back to the key is deliberate: showing `gentilicio` tells you
    exactly what to add to the table, where showing nothing does not.

    Only string entries count, which is what stops `prefix` and `suffix` --
    which sit alongside the language codes in the same mapping -- being
    mistaken for a label.
    """
    spec = table.labels.get(key)
    if not isinstance(spec, dict):
        return key

    def pick(code: str) -> str | None:
        value = spec.get(code)
        return value if isinstance(value, str) else None

    return pick(lang) or pick(default) or key


def _resolve_decoration(
    table: InfoboxTable, key: str, which: str, lang: str, default: str
) -> str:
    """A `prefix` or `suffix`, which may be one string or a per-language map.

    A bare string is used for every language -- right for a symbol like the
    `TL` that precedes a tech level. A map gives one form per language, for
    real words like `años` / `歳`.
    """
    spec = table.labels.get(key)
    if not isinstance(spec, dict):
        return ""
    value = spec.get(which)
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get(lang) or value.get(default) or "")
    return ""


def resolve_prefix(table: InfoboxTable, key: str, lang: str, default: str) -> str:
    return _resolve_decoration(table, key, "prefix", lang, default)


def resolve_suffix(table: InfoboxTable, key: str, lang: str, default: str) -> str:
    return _resolve_decoration(table, key, "suffix", lang, default)


# --- values -----------------------------------------------------------------


def resolve_positional_values(
    table: InfoboxTable,
    frontmatter: dict[str, Any],
    key: str,
    lang: str,
    default: str,
) -> list[str | None]:
    """Values for one key, keeping the holes.

    Paired keys line up by position -- `clase: [Kineticista, Guardiana]` with
    `subclase: [, Tierra]` means the subclass belongs to the *second* class --
    so a gap carries meaning and must not be squeezed out. `resolve_value`
    drops the holes on top of this, since for a plain row a gap is just
    nothing to show.

    Precedence: `key_<lang>` -> `key`, then the shared vocabulary, and the
    vocabulary is consulted only when the note supplied no translation of its
    own. In the default language neither step happens at all.
    """
    is_default = _is_default(lang, default)

    translated = None if is_default else frontmatter.get(f"{key}_{lang}")
    raw = translated if translated is not None else frontmatter.get(key)
    if raw is None or raw == "":
        return []

    source = raw if isinstance(raw, list) else [raw]
    items: list[str | None] = []
    for item in source:
        text = "" if item is None else str(item).strip()
        items.append(text or None)

    # Only map through the shared vocabulary when the note did not supply its
    # own translation -- otherwise a hand-written value would be silently
    # overridden by the table.
    if translated is not None or is_default:
        return items
    vocab = table.values.get(key)
    if not isinstance(vocab, dict):
        return items

    def translate(item: str | None) -> str | None:
        if item is None:
            return None
        entry = vocab.get(item)
        if isinstance(entry, dict):
            return str(entry.get(lang) or item)
        return item

    return [translate(item) for item in items]


def resolve_value(
    table: InfoboxTable,
    frontmatter: dict[str, Any],
    key: str,
    lang: str,
    default: str,
) -> list[str] | None:
    """As `resolve_positional_values`, with the holes dropped.

    None means "the note says nothing about this", which the caller renders as
    a placeholder or drops entirely depending on whether the row is optional.
    """
    items = [item for item in resolve_positional_values(table, frontmatter, key, lang, default)
             if item is not None]
    return items or None


# --- rows -------------------------------------------------------------------


def _layout_sections(layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten either layout shape into a list of sections."""
    sections = layout.get("sections")
    if isinstance(sections, list) and sections:
        return [s for s in sections if isinstance(s, dict)]
    rows = layout.get("rows")
    if isinstance(rows, list) and rows:
        return [{"heading": layout.get("heading"), "rows": rows}]
    return []


def _single_row(
    table: InfoboxTable,
    frontmatter: dict[str, Any],
    entry: str,
    lang: str,
    default: str,
) -> list[ResolvedRow]:
    spec = parse_row_spec(entry)
    values = resolve_value(table, frontmatter, spec.key, lang, default)

    # An optional row with nothing to say disappears. A mandatory row keeps its
    # place with a placeholder, so an infobox is a fixed shape per NoteType and
    # doubles as a checklist of what the note still needs.
    if values is None and spec.optional:
        return []

    return [
        ResolvedRow(
            key=spec.key,
            label=resolve_label(table, spec.key, lang, default),
            values=values if values is not None else [table.placeholder],
            # Decorations dress a real value; a missing one takes neither,
            # since "- años" would read as an actual quantity.
            prefix=resolve_prefix(table, spec.key, lang, default) if values else "",
            suffix=resolve_suffix(table, spec.key, lang, default) if values else "",
            missing=values is None,
        )
    ]


def _group_rows(
    table: InfoboxTable,
    frontmatter: dict[str, Any],
    entry: dict[str, Any],
    lang: str,
    default: str,
    orphan_label,
) -> list[ResolvedRow]:
    """Keys paired by position, for multiclass characters and the like.

    `clase: [Rogue, Maga]` + `subclase: [Arcane Trickster, Bladesinger]`
    becomes class 1 / subclass 1 / class 2 / subclass 2, rather than two rows
    of slash-separated lists.

    Asymmetry is expected in one direction only. A class with no subclass is
    normal, and the optional subclass row simply vanishes for that position. A
    subclass with no class is a mistake, so that row is kept and flagged rather
    than hidden -- the whole point is that the author sees it.
    """
    specs = [parse_row_spec(key) for key in (entry.get("group") or [])]
    if not specs:
        return []

    columns = [
        resolve_positional_values(table, frontmatter, spec.key, lang, default)
        for spec in specs
    ]
    positions = max((len(column) for column in columns), default=0)

    if positions == 0:
        # Nothing anywhere in the group: fall back to plain per-key behaviour
        # so a mandatory key still shows its placeholder row.
        return [
            row
            for spec in specs
            for row in _single_row(
                table, frontmatter,
                f"{spec.key}?" if spec.optional else spec.key,
                lang, default,
            )
        ]

    parent = specs[0]
    rows: list[ResolvedRow] = []

    for index in range(positions):
        parent_missing = _at(columns[0], index) is None

        for column, spec in enumerate(specs):
            value = _at(columns[column], index)
            is_parent = column == 0

            if value is None and spec.optional:
                continue
            if value is None and not is_parent and parent_missing:
                continue

            label = resolve_label(table, spec.key, lang, default)
            rows.append(
                ResolvedRow(
                    key=spec.key,
                    # Only the parent is numbered, and only when the group
                    # actually repeats. Children are already bound to it
                    # visually by the "|>" in their label, so numbering them
                    # too would be noise; a single-class note stays plain.
                    label=f"{label} {index + 1}" if is_parent and positions > 1 else label,
                    values=[table.placeholder] if value is None else [value],
                    prefix="" if value is None else resolve_prefix(table, spec.key, lang, default),
                    suffix="" if value is None else resolve_suffix(table, spec.key, lang, default),
                    missing=value is None,
                    problem=(
                        orphan_label(spec.key, parent.key)
                        if value is not None and not is_parent and parent_missing
                        else ""
                    ),
                )
            )

    return rows


def _at(column: list[str | None], index: int) -> str | None:
    return column[index] if index < len(column) else None


def _default_orphan_label(child: str, parent: str) -> str:
    return f"«{child}» no tiene «{parent}» en esa posición"


def resolve_infobox(
    table: InfoboxTable,
    layout: dict[str, Any],
    frontmatter: dict[str, Any],
    lang: str,
    default: str,
    orphan_label=_default_orphan_label,
) -> list[ResolvedSection]:
    """Build the display model for one note.

    Every row the layout declares is kept whether or not the note fills it in:
    an infobox is a fixed shape per NoteType, so a half-written note shows the
    same table as a finished one with a placeholder where a value is missing.
    Rows marked optional with a trailing `?` are the exception.
    """
    sections: list[ResolvedSection] = []
    for section in _layout_sections(layout):
        heading_key = section.get("heading")
        rows: list[ResolvedRow] = []
        for entry in section.get("rows") or []:
            if isinstance(entry, dict):
                rows.extend(_group_rows(table, frontmatter, entry, lang, default, orphan_label))
            else:
                rows.extend(_single_row(table, frontmatter, str(entry), lang, default))
        sections.append(
            ResolvedSection(
                heading=resolve_label(table, str(heading_key), lang, default) if heading_key else "",
                rows=rows,
            )
        )
    return sections


# --- title, status, image ---------------------------------------------------


def resolve_title(
    layout: dict[str, Any],
    frontmatter: dict[str, Any],
    lang: str,
    default: str,
    basename: str,
) -> str:
    """`title_<lang>` -> `title` -> the filename."""
    key = str(layout.get("title") or "title")
    translated = None if _is_default(lang, default) else frontmatter.get(f"{key}_{lang}")
    value = translated if translated is not None else frontmatter.get(key)
    text = value.strip() if isinstance(value, str) else ""
    return text or basename


def resolve_status(
    table: InfoboxTable,
    layout: dict[str, Any],
    frontmatter: dict[str, Any],
    lang: str,
    default: str,
) -> ResolvedStatus | None:
    """A superscript marker beside the title, or None for no marker at all.

    Obsidian has no enum property type, so the closed set lives in the table
    and the only enforcement is visual: a value outside the set falls through
    to the entry marked `fallback: true`, which makes a typo visible instead of
    silent.

    An absent or empty key shows nothing. That is deliberate -- the ordinary
    case is "alive", it needs no glyph, and a note nobody has annotated should
    not be flagged as unknown.
    """
    key = layout.get("status")
    if not key or not table.status:
        return None

    raw = frontmatter.get(str(key))
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(raw, (int, float, bool)):
        text = str(raw)
    else:
        # Only a scalar can name a status; a list or a mapping is a mistake,
        # not a value.
        text = ""
    if not text:
        return None

    wanted = normalize_type_key(text)
    matched = None
    for name, spec in table.status.items():
        if normalize_type_key(name) == wanted and isinstance(spec, dict):
            matched = spec
            break

    spec = matched
    if spec is None:
        spec = next(
            (s for s in table.status.values() if isinstance(s, dict) and s.get("fallback") is True),
            None,
        )
    if spec is None:
        return None

    def pick(code: str) -> str | None:
        value = spec.get(code)
        return value if isinstance(value, str) else None

    symbol = spec.get("symbol")
    return ResolvedStatus(
        symbol=symbol if isinstance(symbol, str) else "",
        tooltip=pick(lang) or pick(default) or text,
        fallback=matched is None,
    )


def normalize_image_ref(raw: Any) -> str | None:
    """Reduce an image frontmatter value to a bare vault reference.

    The vault is not uniform: alongside plain `castle-flag.svg` there are
    already-wrapped `[[Sonia.png]]`, a stray trailing `Sonia.png]]`, and the
    untouched template placeholders `![[]]` and `[[]]`. Peeling the wrappers
    means exactly one embed is built, and an empty placeholder yields None so
    the note shows no image rather than a broken one. Any `|size` the author
    wrote inside the brackets survives.
    """
    if not isinstance(raw, str):
        return None
    inner = raw.strip()
    inner = re.sub(r"^!?\[\[", "", inner)
    inner = re.sub(r"\]\]$", "", inner)
    inner = inner.strip()
    return inner or None


def resolve_image(
    table: InfoboxTable,
    layout: dict[str, Any],
    frontmatter: dict[str, Any],
    lang: str,
    default: str,
) -> tuple[str | None, str | None]:
    """(reference, size) for the layout's image, either possibly None.

    Routed through the ordinary value resolution so `imagen_ja` works like any
    other key -- a map or diagram with translated labels is a real case in a
    world atlas. Only the first value is used.
    """
    key = layout.get("image")
    if not key:
        return None, None

    values = resolve_value(table, frontmatter, str(key), lang, default)
    ref = normalize_image_ref(values[0]) if values else None
    if not ref:
        # A layout may name a stand-in for notes with no picture of their own.
        # This is an extension of the plugin's model, proposed back to it so
        # that Obsidian and the site agree on what a portrait-less note looks
        # like; until the plugin has it, the parser also accepts the same
        # mapping from `fallback_images:` in vault.config.yaml.
        return normalize_image_ref(layout.get("imageFallback")), None

    size = layout.get("imageSize")
    # Never override a size the author already wrote inside the link.
    if size and "|" not in ref:
        return ref, str(size)
    if "|" in ref:
        ref, _, inline = ref.partition("|")
        return ref.strip(), inline.strip() or None
    return ref, None
