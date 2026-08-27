"""Ingest.

Pass 1 of the two-pass design: walk the vault, parse every note, assign slugs,
and build the global index. Nothing here transforms note bodies -- `raw_body`
is copied to `body` untouched and handed to the pipeline.

Cross-note resolution is why this is a separate pass: a module rewriting note A
in pass 2 needs note B already indexed, aliases and all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import frontmatter, mdtext, vault
from .assets import AssetIndex
from .config import Config
from .model import Note, VaultContext, index_key
from .slugs import SlugRegistry
from .vault import VaultFile

# An inline tag: `#Territorio`, `#custodios-de-juutei`, `#Ciencia/Alquimia`.
# Requires a non-word char before it so `#` inside a URL or a heading is safe.
_INLINE_TAG = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[]))\#([A-Za-zÀ-ɏ][\wÀ-ɏ/-]*)"
)

# Fenced code blocks, so tags inside them are not harvested.
_FENCE = re.compile(r"^(?P<fence>```|~~~).*?^(?P=fence)\s*$", re.DOTALL | re.MULTILINE)

#: Distinguishes a missing `publish` key from one present but empty.
_ABSENT = object()


@dataclass
class PublishKeyGap:
    """A note with real content whose frontmatter never mentions `publish`."""

    vault_path: str
    title: str
    body_lines: int
    #: Body lines that are actual prose -- excludes callout/infobox lines,
    #: table rows, headings, bare tags and fenced blocks. A note with many
    #: `body_lines` but zero `prose_lines` is untouched template scaffolding.
    prose_lines: int
    #: True when the key exists but is empty (`publish:`) rather than absent.
    key_present_but_empty: bool


@dataclass
class IngestResult:
    ctx: VaultContext
    skipped_unpublished: list[str] = field(default_factory=list)
    skipped_uncollected: list[str] = field(default_factory=list)
    slug_collisions: list[tuple[str, str, str]] = field(default_factory=list)
    publish_key_gaps: list[PublishKeyGap] = field(default_factory=list)


def ingest(config: Config, asset_index: AssetIndex) -> IngestResult:
    """Walk, parse and index every markdown note in the vault."""
    ctx = VaultContext(config=config)
    result = IngestResult(ctx=ctx)
    registry = SlugRegistry()

    # `walk_markdown` returns files sorted by relative path. That ordering is
    # what makes slug collision tiebreaks stable across runs.
    for found in vault.walk_markdown(config):
        collection = _collection_for(found, config)
        if collection is None:
            result.skipped_uncollected.append(found.relative)
            continue

        note, parsed = _build_note(found, collection, config, registry)
        ctx.notes.append(note)
        _index(ctx, note)

        if not note.published:
            result.skipped_unpublished.append(found.relative)

        gap = _publish_key_gap(note, parsed, config)
        if gap is not None:
            result.publish_key_gaps.append(gap)

    result.slug_collisions = list(registry.collisions)
    ctx.assets = {key: asset for key, asset in asset_index.by_key.items()}
    return result


def _collection_for(found: VaultFile, config: Config) -> str | None:
    """Map a note to a collection by its top-level vault folder."""
    if not found.parent_parts:
        return None  # root-level notes (index.md, README.md) are not content
    return config.collections.get(found.parent_parts[0])


def _publish_key_gap(
    note: Note, parsed: frontmatter.FrontmatterResult, config: Config
) -> PublishKeyGap | None:
    """Flag a note that has content and frontmatter but no `publish` decision.

    Deliberately narrow, so the list stays actionable rather than becoming the
    whole vault: notes with no frontmatter at all, and notes whose body is
    shorter than `publish_hint_min_lines`, are template stubs the author has
    not started yet and are left out.
    """
    if not parsed.had_frontmatter:
        return None

    value = parsed.data.get("publish", _ABSENT)
    if value is not _ABSENT and value is not None:
        return None  # an explicit decision was made either way

    body_lines = sum(1 for line in parsed.body.splitlines() if line.strip())
    if body_lines < config.publish_hint_min_lines:
        return None

    return PublishKeyGap(
        vault_path=note.vault_path,
        title=note.title,
        body_lines=body_lines,
        prose_lines=_count_prose_lines(parsed.body),
        key_present_but_empty=value is None,
    )


def _count_prose_lines(body: str) -> int:
    """Body lines that carry authored prose rather than template structure.

    The templates in `z_Templates/` produce ~20 lines of infobox callout,
    table rows and headings before the author writes anything, so a raw line
    count cannot tell a started note from an untouched one.
    """
    count = 0
    for line in mdtext.prose_only(body).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Callout/blockquote body, table row, heading, or a line that is only
        # tags -- all structure the template supplied.
        if stripped.startswith((">", "|", "#")):
            continue
        if all(word.startswith("#") for word in stripped.split()):
            continue
        count += 1
    return count


def _build_note(
    found: VaultFile, collection: str, config: Config, registry: SlugRegistry
) -> tuple[Note, frontmatter.FrontmatterResult]:
    text = vault.read_text(found.path, config.vault_root)
    parsed = frontmatter.parse(text)

    title = _title_for(parsed.data, found)
    slug = registry.assign(title, found.parts)
    breadcrumb = list(found.parent_parts)

    # A folder note *is* its folder, so the last segment repeats the note's own
    # title: `Gran Pantano` would show `... / Rodinia / Gran Pantano` above a
    # heading that already says Gran Pantano. Drop it -- the trail should say
    # where the note sits, not name it twice.
    if _is_folder_note(found) and breadcrumb:
        breadcrumb.pop()

    note = Note(
        vault_path=found.relative,
        source=found.path,
        collection=collection,
        slug=slug,
        title=title,
        breadcrumb=breadcrumb,
        is_folder_note=_is_folder_note(found),
        source_modified=vault.modified_at(found.path, config.vault_root),
        frontmatter=parsed.data,
        raw_body=parsed.body,
        body=parsed.body,
        aliases=_string_list(parsed.data.get("aliases")),
        tags=_collect_tags(parsed.data, parsed.body),
        frontmatter_refs=parsed.refs,
        published=_is_published(parsed.data, config),
    )

    for warning in parsed.warnings:
        note.warn(warning)
    if not parsed.had_frontmatter:
        note.warn("no frontmatter; title taken from filename")

    return note, parsed


def _title_for(data: dict, found: VaultFile) -> str:
    """Explicit `title` frontmatter, else the filename.

    The filename is a note's real identity in Obsidian -- it is what wikilinks
    resolve against -- so it is the fallback rather than any other field.
    `nombre` looks like a title but is a template artifact: 7 notes still carry
    a literal `nombre: "Organizacion"` from the Organizacion template, which
    would collapse them all onto one slug.
    """
    value = data.get("title")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return found.stem


def _is_folder_note(found: VaultFile) -> bool:
    """`Luma/Luma.md` is the note *for* the folder, per the folder-notes plugin.

    Detection lives here because it affects slugs; the presentation rule
    (folder notes do not appear as children of their own folder) is `nav_tree`'s.
    """
    parents = found.parent_parts
    return bool(parents) and parents[-1] == found.stem


def _is_published(data: dict, config: Config) -> bool:
    value = data.get("publish")
    if value is None:
        return config.default_publish
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        # `item is not None` first: a positional hole would otherwise be
        # stringified to the literal "None", which is truthy and would land in
        # `aliases` as a real alias.
        return [
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        ]
    return [str(value).strip()]


def _collect_tags(data: dict, body: str) -> list[str]:
    """Merge frontmatter tags with inline body tags, de-duplicated, sorted.

    `cleanup` will strip inline tags from the prose; collecting them now means
    they are queryable (`dataview_queries` selects on `#inquisidor`) even before that.
    """
    tags: list[str] = []
    seen: set[str] = set()

    for raw in _string_list(data.get("tags")) + _string_list(data.get("tag")):
        tag = raw.lstrip("#").strip()
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            tags.append(tag)

    for match in _INLINE_TAG.finditer(_FENCE.sub("", body)):
        tag = match.group(1)
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            tags.append(tag)

    return sorted(tags, key=str.casefold)


def _index(ctx: VaultContext, note: Note) -> None:
    """Register a note in every lookup table, first writer wins.

    Order matters on conflict: an explicit path or title should beat an alias,
    which is why `VaultContext.find_note` consults the tables in that order
    rather than relying on a single merged map.
    """
    ctx.by_vault_path.setdefault(index_key(note.vault_path), note)
    ctx.by_slug.setdefault(index_key(note.slug), note)
    ctx.by_title.setdefault(index_key(note.title), note)

    # Obsidian also resolves `[[Luma]]` against the *filename*, which differs
    # from the title when frontmatter overrides it.
    stem = note.vault_path.rsplit("/", 1)[-1]
    ctx.by_title.setdefault(index_key(stem), note)

    for alias in note.aliases:
        ctx.by_alias.setdefault(index_key(alias), note)
