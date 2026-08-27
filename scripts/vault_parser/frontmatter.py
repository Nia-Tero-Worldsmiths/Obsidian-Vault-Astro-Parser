"""Tolerant YAML frontmatter parsing.

Obsidian's property editor emits YAML that PyYAML will not accept as-is. Two
real cases from this vault drive the design:

    imagen: ![[]] #Borrar parentesis una vez importada la imagen
        `!` opens a YAML tag, so this is a hard ConstructorError.

    padre: [[Norman I - Libro Harapiento]]
        Valid YAML, but parses to the nested list [['Norman I - ...']],
        silently destroying the wikilink.

So bare `[[...]]` values are quoted *before* parsing, which both fixes the
error and preserves the link text for `links` to resolve later.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from .model import WikiRef

# Frontmatter must be the very first thing in the file.
_FRONTMATTER = re.compile(r"\A---\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

# A scalar value (after `key:` or a `- ` list bullet) that is a bare wikilink,
# optionally with a trailing `# comment`.
_BARE_LINK_VALUE = re.compile(
    r"""^
    (?P<prefix>\s* (?: -[ \t]+ | [^:\n]+?:[ \t]+ ) )
    (?P<value> !?\[\[ [^\n]*? \]\] )
    (?P<trail> [ \t]* (?:\#[^\n]*)? )
    $""",
    re.VERBOSE,
)

# Values that mean "the author has not filled this in yet".
_EMPTY_PLACEHOLDERS = {"", "[[]]", "![[]]", "[[ ]]", "-"}

#: Distinguishes "this list is not a mangled wikilink" from "it was, and it
#: was the empty placeholder" -- both of which would otherwise be None.
_NOT_A_LINK = object()


class FrontmatterResult:
    """Parsed frontmatter plus everything `ingest` needs to report on it."""

    __slots__ = ("data", "body", "refs", "warnings", "had_frontmatter")

    def __init__(
        self,
        data: dict[str, Any],
        body: str,
        refs: list[WikiRef],
        warnings: list[str],
        had_frontmatter: bool,
    ) -> None:
        self.data = data
        self.body = body
        self.refs = refs
        self.warnings = warnings
        self.had_frontmatter = had_frontmatter


def parse(text: str) -> FrontmatterResult:
    """Split and parse a note's frontmatter, never raising on bad YAML."""
    match = _FRONTMATTER.match(text)
    if not match:
        return FrontmatterResult({}, text, [], [], had_frontmatter=False)

    raw_yaml = match.group("yaml")
    body = text[match.end():]
    warnings: list[str] = []

    sanitized, quoted_lines = _sanitize(raw_yaml)
    try:
        data = yaml.safe_load(sanitized)
    except yaml.YAMLError as exc:
        warnings.append(f"unparseable YAML frontmatter ({_brief(exc)}); treated as empty")
        return FrontmatterResult({}, body, [], warnings, had_frontmatter=True)

    if data is None:
        data = {}
    elif not isinstance(data, dict):
        warnings.append(
            f"frontmatter is a {type(data).__name__}, not a mapping; treated as empty"
        )
        return FrontmatterResult({}, body, [], warnings, had_frontmatter=True)

    if quoted_lines:
        warnings.append(
            f"quoted {quoted_lines} unquoted wikilink value(s) to keep them parseable"
        )

    data, normalize_warnings = _normalize(data)
    warnings.extend(normalize_warnings)

    return FrontmatterResult(
        data=data,
        body=body,
        refs=collect_refs(data),
        warnings=warnings,
        had_frontmatter=True,
    )


# A single-line flow sequence, i.e. `[a, b]`. The lookarounds keep
# `[[Wikilink]]` out: the inner bracket is preceded by `[`, and the outer one is
# followed by `[`, which the character class cannot match.
_FLOW_SEQUENCE = re.compile(r"(?<!\[)\[([^\[\]\n]*)\](?!\])")

# A bare key carrying the infobox layout's optional-row marker, e.g.
# `subclase?`. Only meaningful in the translation table, but normalising it
# here keeps one definition of "what PyYAML needs help with".
_OPTIONAL_ITEM = re.compile(r"^[\w.-]+\?$")


def normalize_flow_sequences(text: str) -> str:
    """Rewrite a flow sequence into something PyYAML will accept.

    Two shapes the vault writes are legal to Obsidian's YAML parser and
    rejected by PyYAML, and both matter:

    * **A hole.** The infobox pairs keys by position, so
      `clase: [Kineticista, Guardiana]` with `subclase: [, Tierra]` means the
      subclass belongs to the *second* class -- the spelling the vault's own
      translation table documents. PyYAML refuses an empty item outright, and
      because `parse` treats an unparseable block as empty, such a note would
      lose its whole frontmatter, `publish` included, and drop off the site
      without a word. An explicit `null` means the same thing and parses.

    * **The optional-row marker,** `[clase, subclase?]`. `?` is an indicator
      character in flow context; quoting it keeps the value identical.

    Same move as `_sanitize` makes for bare wikilinks: meet the vault where it
    is, rather than asking it to be edited to suit one of its two readers.
    """

    def fix(match: "re.Match[str]") -> str:
        items = [item.strip() for item in match.group(1).split(",")]
        if len(items) == 1 and not items[0]:
            return "[]"  # an empty list, not a one-element list of nothing
        rendered = [
            "null"
            if not item
            else (f'"{item}"' if _OPTIONAL_ITEM.match(item) else item)
            for item in items
        ]
        return "[" + ", ".join(rendered) + "]"

    return _FLOW_SEQUENCE.sub(fix, text)


def _sanitize(raw: str) -> tuple[str, int]:
    """Quote bare `[[...]]` and `![[...]]` values so PyYAML accepts them."""
    lines = raw.split("\n")
    quoted = 0

    for index, line in enumerate(lines):
        lines[index] = line = normalize_flow_sequences(line)
        match = _BARE_LINK_VALUE.match(line.rstrip("\r"))
        if not match:
            continue
        value = match.group("value")
        lines[index] = f"{match.group('prefix')}{_yaml_quote(value)}"
        quoted += 1

    return "\n".join(lines), quoted


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _normalize(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Clean up author placeholders and legacy keys."""
    warnings: list[str] = []
    result: dict[str, Any] = {}

    for key, value in data.items():
        key = str(key)
        cleaned = _clean_value(value)

        # `cssclass` is the legacy singular spelling (6 notes still use it).
        if key == "cssclass":
            key = "cssclasses"
            if cleaned is not None and not isinstance(cleaned, list):
                cleaned = [cleaned]
            warnings.append("migrated legacy `cssclass` to `cssclasses`")

        if key in result and result[key] not in (None, [], ""):
            warnings.append(f"duplicate frontmatter key `{key}`; kept the first value")
            continue

        result[key] = cleaned

    return result, warnings


def _clean_value(value: Any) -> Any:
    """Collapse the vault's various 'not filled in yet' shapes to None."""
    if value is None:
        return None

    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped in _EMPTY_PLACEHOLDERS else stripped

    if isinstance(value, list):
        # `imagen: [[]]` parses to [[]] and `padre: [[X]]` to [['X']]. Both are
        # a wikilink the sanitizer did not reach (e.g. inside a flow sequence).
        recovered = _recover_nested_link(value)
        if recovered is not _NOT_A_LINK:
            return recovered

        items = [_clean_value(item) for item in value]
        items = [item for item in items if item != []]

        # Interior holes are KEPT, as None. The infobox pairs keys by
        # position, so `clase: [Kineticista, Guardiana]` with
        # `subclase: [, Tierra]` means the subclass belongs to the *second*
        # class; squeezing the gap out would silently attach it to the first.
        # Consumers that want plain strings filter None themselves --
        # `_string_list` here, `inline_dataview` and `dataview_queries` for
        # rendering.
        #
        # A list that is nothing but holes still says nothing at all.
        if not any(item is not None for item in items):
            return None
        return items

    if isinstance(value, dict):
        cleaned = {str(k): _clean_value(v) for k, v in value.items()}
        return cleaned or None

    return value


def _recover_nested_link(value: list) -> Any:
    """Turn YAML's reading of an unquoted wikilink back into link text.

    `[[]]` -> None (empty placeholder), `[['Norman I']]` -> '[[Norman I]]'.

    Returns the `_NOT_A_LINK` sentinel rather than None when the value is an
    ordinary list, since None is itself a meaningful result here.
    """
    if len(value) != 1 or not isinstance(value[0], list):
        return _NOT_A_LINK
    inner = value[0]
    if not inner:
        return None  # `[[]]` -- the unfilled placeholder
    if all(isinstance(item, str) for item in inner):
        return f"[[{', '.join(inner)}]]"
    return _NOT_A_LINK


def collect_refs(data: dict[str, Any], *, origin: str | None = None) -> list[WikiRef]:
    """Find every wikilink hiding inside frontmatter values.

    Notes reference each other through YAML as much as through prose --
    `lugarNacimiento`, `organizacion`, `padre`, `anterior`, `siguiente` are all
    links -- so `links` needs these alongside the body ones.
    """
    refs: list[WikiRef] = []

    def visit(value: Any, key: str | None) -> None:
        if isinstance(value, str):
            refs.extend(parse_links(value, origin_key=key))
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, dict):
            for sub_key, item in value.items():
                visit(item, f"{key}.{sub_key}" if key else str(sub_key))

    for key, value in data.items():
        visit(value, origin or str(key))

    return refs


def parse_links(text: str, *, origin_key: str | None = None) -> list[WikiRef]:
    """Extract every `[[...]]` / `![[...]]` reference from a string.

    Thin alias over `linking.parse_refs`, kept because frontmatter parsing is
    where most callers reach for it.
    """
    from . import linking

    return linking.parse_refs(text, origin_key=origin_key)


def _brief(exc: Exception) -> str:
    return " ".join(str(exc).split())[:120]
