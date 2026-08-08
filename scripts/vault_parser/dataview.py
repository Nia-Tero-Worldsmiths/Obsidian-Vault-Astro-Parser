"""A deliberately small parser for the dataview queries this vault uses.

Not a dataview implementation. The vault contains 20 fenced queries in three
forms, and this parses exactly those, refusing anything else so an unsupported
query is reported rather than silently mis-rendered:

    TABLE without ID embed(link(imagen, "500x500")) as "Portrait",
                     file.link as "Nombre"
    FROM #inquisidor AND !#ex-inquisidor
    SORT title ASC

    table without id file.link as "Lista de capitulos"
    where padre = this.file.link

    list
    where ubicacion = this.file.link

One trap worth naming: several queries carry a trailing `//` comment that
itself contains a tag --

    FROM #seguidoresDeLaHoja //Filtra por tag aqui, ej. #comendador

-- so comments must be stripped before tags are read, or the query silently
gains a second filter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Clause keywords that end the previous clause.
_CLAUSE = re.compile(r"\b(FROM|WHERE|SORT|GROUP\s+BY|FLATTEN|LIMIT)\b", re.IGNORECASE)

# `//` to end of line, when not inside a quoted string.
_COMMENT = re.compile(r'(?<!:)//(?=(?:[^"]*"[^"]*")*[^"]*$).*$', re.MULTILINE)

# `embed(link(imagen, "500x500"))`.
#
# The second argument to `link()` is its *display* text -- but for an embed,
# Obsidian reads the display as dimensions, exactly as `![[img.jpg|500x500]]`
# does. So `"500x500"` is a size, and dropping it is why cards whose portraits
# have different aspect ratios render at different heights here while Obsidian
# shows them uniformly.
_EMBED_COLUMN = re.compile(
    r"^embed\(\s*link\(\s*(?P<field>[A-Za-zÀ-ÿ_][\w.]*)\s*"
    r"""(?:,\s*(?P<quote>["'])(?P<size>[^"']*)(?P=quote)\s*)?\)\s*\)$""",
    re.IGNORECASE,
)

# An embed display that is really a size: `500x500`, or `500` for width only.
_SIZE_HINT = re.compile(r"^\s*(?P<width>\d+)\s*(?:x\s*(?P<height>\d+)\s*)?$")
_ALIAS = re.compile(r'\s+AS\s+(?P<quote>["\'])(?P<alias>.*?)(?P=quote)\s*$', re.IGNORECASE)
_ALIAS_BARE = re.compile(r"\s+AS\s+(?P<alias>\S+)\s*$", re.IGNORECASE)

_TAG = re.compile(r"(?P<negate>[!-])?#(?P<tag>[A-Za-zÀ-ÿ0-9_/-]*)")
_EQUALS_THIS = re.compile(
    r"^(?P<field>[A-Za-zÀ-ÿ_][\w]*)\s*=\s*this\.file\.link$", re.IGNORECASE
)
_TRUTHY = re.compile(r"^(?P<field>[A-Za-zÀ-ÿ_][\w]*)$")


def _parse_size(value: str | None) -> tuple[int, int | None] | None:
    """Read an embed display as dimensions, the way Obsidian does.

    Returns None when the display is ordinary text rather than a size, in
    which case Obsidian would treat it as a caption and not resize anything.
    """
    if not value:
        return None
    match = _SIZE_HINT.match(value)
    if not match:
        return None
    height = match.group("height")
    return int(match.group("width")), int(height) if height else None


class UnsupportedQuery(Exception):
    """The query uses syntax this parser does not implement."""


@dataclass
class Column:
    kind: str  # "file_link" | "embed" | "field"
    field: str | None = None
    alias: str | None = None
    #: For embeds, the `(width, height)` Obsidian would render at. Height is
    #: None when only a width was given.
    size: tuple[int, int | None] | None = None

    @property
    def header(self) -> str:
        if self.alias:
            return self.alias
        if self.kind == "file_link":
            return "File"
        return self.field or ""


@dataclass
class TagFilter:
    terms: list[tuple[bool, str]] = field(default_factory=list)  # (negated, tag)
    operator: str = "AND"


@dataclass
class Condition:
    kind: str  # "equals_this" | "truthy"
    field: str


@dataclass
class SortKey:
    field: str
    #: True for `SORT number(file.name)`, which sorts by the first number in
    #: the value rather than lexicographically.
    numeric: bool = False
    descending: bool = False


@dataclass
class Query:
    form: str  # "table" | "list"
    columns: list[Column] = field(default_factory=list)
    without_id: bool = False
    tags: TagFilter | None = None
    where: Condition | None = None
    sort: SortKey | None = None
    warnings: list[str] = field(default_factory=list)


def parse(source: str) -> Query:
    text = _COMMENT.sub("", source).strip()
    if not text:
        raise UnsupportedQuery("empty query")

    clauses = _split_clauses(text)
    head = clauses.pop("_head")

    form, remainder = _parse_head(head)
    query = Query(form=form)

    remainder, query.without_id = _strip_without_id(remainder)
    if form == "table":
        query.columns = _parse_columns(remainder)
    elif remainder.strip():
        # `LIST <expr>` renders that expression instead of the file link.
        # Unused here; noted rather than silently ignored.
        query.warnings.append(f"ignoring LIST expression: {remainder.strip()}")

    if "from" in clauses:
        query.tags = _parse_from(clauses["from"], query)
    if "where" in clauses:
        query.where = _parse_where(clauses["where"])
    if "sort" in clauses:
        query.sort = _parse_sort(clauses["sort"])

    for unsupported in ("group by", "flatten", "limit"):
        if unsupported in clauses:
            raise UnsupportedQuery(f"{unsupported.upper()} is not supported")

    return query


def _split_clauses(text: str) -> dict[str, str]:
    matches = list(_CLAUSE.finditer(text))
    clauses: dict[str, str] = {}

    head_end = matches[0].start() if matches else len(text)
    clauses["_head"] = text[:head_end].strip()

    for index, match in enumerate(matches):
        name = " ".join(match.group(1).lower().split())
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        clauses[name] = text[match.end() : end].strip()

    return clauses


def _parse_head(head: str) -> tuple[str, str]:
    stripped = head.strip()
    lowered = stripped.lower()
    for keyword, form in (("table", "table"), ("list", "list")):
        if lowered == keyword or lowered.startswith(keyword + " ") or lowered.startswith(
            keyword + "\n"
        ):
            return form, stripped[len(keyword) :]
    raise UnsupportedQuery(f"unsupported query type: {stripped.split() or ['(empty)']}")


def _strip_without_id(text: str) -> tuple[str, bool]:
    match = re.match(r"^\s*WITHOUT\s+ID\b", text, re.IGNORECASE)
    if match:
        return text[match.end() :], True
    return text, False


def _parse_columns(text: str) -> list[Column]:
    columns: list[Column] = []
    for piece in _split_top_level(text):
        piece = piece.strip()
        if not piece:
            continue

        alias = None
        alias_match = _ALIAS.search(piece) or _ALIAS_BARE.search(piece)
        if alias_match:
            alias = alias_match.group("alias")
            piece = piece[: alias_match.start()].strip()

        embed = _EMBED_COLUMN.match(piece)
        if embed:
            columns.append(
                Column("embed", embed.group("field"), alias, _parse_size(embed.group("size")))
            )
        elif piece.lower() == "file.link":
            columns.append(Column("file_link", None, alias))
        elif re.fullmatch(r"[A-Za-zÀ-ÿ_][\w]*", piece):
            columns.append(Column("field", piece, alias))
        else:
            raise UnsupportedQuery(f"unsupported column expression: {piece}")
    return columns


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside parentheses or quotes."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []

    for char in text:
        if quote:
            if char == quote:
                quote = None
            current.append(char)
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)

    parts.append("".join(current))
    return parts


def _parse_from(text: str, query: Query) -> TagFilter:
    operator = "OR" if re.search(r"\bOR\b", text, re.IGNORECASE) else "AND"
    if re.search(r"\bOR\b", text, re.IGNORECASE) and re.search(
        r"\bAND\b", text, re.IGNORECASE
    ):
        raise UnsupportedQuery("mixed AND/OR in FROM is not supported")

    terms: list[tuple[bool, str]] = []
    for match in _TAG.finditer(text):
        tag = match.group("tag")
        if not tag:
            # `FROM #` -- an unfilled template placeholder.
            query.warnings.append("FROM has an empty tag; query matches nothing")
            continue
        negated = match.group("negate") is not None or bool(
            re.search(r"\bNOT\s*$", text[: match.start()], re.IGNORECASE)
        )
        terms.append((negated, tag))

    if not terms:
        query.warnings.append("FROM produced no usable tags")
    return TagFilter(terms=terms, operator=operator)


def _parse_where(text: str) -> Condition:
    condition = text.strip()
    equals = _EQUALS_THIS.match(condition)
    if equals:
        return Condition("equals_this", equals.group("field"))
    truthy = _TRUTHY.match(condition)
    if truthy:
        return Condition("truthy", truthy.group("field"))
    raise UnsupportedQuery(f"unsupported WHERE clause: {condition}")


_SORT_NUMBER = re.compile(
    r"^number\(\s*(?P<field>[A-Za-zÀ-ÿ_][\w.]*)\s*\)$", re.IGNORECASE
)


def _parse_sort(text: str) -> SortKey:
    parts = text.split()
    if not parts:
        raise UnsupportedQuery("empty SORT clause")

    descending = len(parts) > 1 and parts[-1].lower() in ("desc", "descending")
    expression = " ".join(parts[:-1]) if descending or parts[-1].lower() in (
        "asc",
        "ascending",
    ) else " ".join(parts)
    expression = expression.strip()

    # `SORT number(file.name)` is the idiomatic dataview fix for filenames that
    # sort as 1, 10, 2 -- dataview's `number()` pulls the first number out of
    # the string (NUMBER_REGEX = /-?[0-9]+(\.[0-9]+)?/, unanchored).
    numeric = _SORT_NUMBER.match(expression)
    if numeric:
        return SortKey(numeric.group("field"), numeric=True, descending=descending)

    if not re.fullmatch(r"[A-Za-zÀ-ÿ_][\w.]*", expression):
        raise UnsupportedQuery(f"unsupported SORT expression: {expression}")

    return SortKey(expression, descending=descending)
