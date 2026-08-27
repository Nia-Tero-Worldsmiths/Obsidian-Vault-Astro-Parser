"""Core data structures passed down the module pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WikiRef:
    """A parsed `[[target|alias]]` reference, wherever it was found.

    `ingest` only records these; `links` resolves them. `raw` is the exact
    source text so a resolver can do a literal replacement without re-parsing.
    """

    raw: str
    target: str
    alias: str | None = None
    heading: str | None = None
    block: str | None = None
    is_embed: bool = False
    # Frontmatter key this came from, or None if it came from the body.
    origin_key: str | None = None
    # Set by `links` (a `linking.Resolution`). Untyped to avoid a circular
    # import; `inline_dataview` and `dataview_queries` read it instead of resolving again.
    resolution: Any = None

    @property
    def display(self) -> str:
        return self.alias or self.target


@dataclass
class Asset:
    """An image discovered in the vault."""

    vault_path: str  # POSIX, vault-relative
    source: Path  # absolute, inside the vault
    slug: str  # output filename stem
    extension: str  # lowercased, with leading dot

    @property
    def out_name(self) -> str:
        return f"{self.slug}{self.extension}"


@dataclass
class Note:
    """One markdown note, mutated as it travels down the pipeline."""

    # --- identity, set by `ingest` -------------------------------------
    vault_path: str  # POSIX, vault-relative, e.g. "World Atlas/.../Luma.md"
    source: Path  # absolute, inside the vault
    collection: str  # Astro collection name
    slug: str
    title: str
    breadcrumb: list[str]  # vault folder chain, excluding the file itself
    is_folder_note: bool = False
    source_modified: str = ""  # ISO-8601 UTC

    # --- content ------------------------------------------------------
    frontmatter: dict[str, Any] = field(default_factory=dict)
    raw_body: str = ""  # body as it appeared in the vault, never mutated
    body: str = ""  # mutated by each module in turn

    # --- languages ----------------------------------------------------
    # Filled by `lang_blocks` (order 15) when the note carries `:::lang`
    # markers. `body` always mirrors the default language's entry, so every
    # module written before i18n existed keeps working untouched; the pipeline
    # driver runs each module once per entry here. Empty means monolingual.
    lang_bodies: dict[str, str] = field(default_factory=dict)
    #: Codes this note actually has content for, in configured order.
    languages: list[str] = field(default_factory=list)
    #: Which language `body` currently holds. The pipeline sets this around
    #: each per-language pass so a module can resolve `key_<lang>` frontmatter
    #: or pick a translated link label without being handed a new signature.
    active_language: str = ""

    # --- derived ------------------------------------------------------
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # Wikilinks found inside YAML values, for `links`.
    frontmatter_refs: list[WikiRef] = field(default_factory=list)
    published: bool = False
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def url(self) -> str:
        return f"/{self.slug}"

    @property
    def out_path(self) -> str:
        """Path relative to the content output root."""
        return f"{self.collection}/{self.slug}.md"


@dataclass
class VaultContext:
    """The read-only global index, available to every module.

    Built in pass 1 so that a module transforming note A in pass 2 can resolve
    a reference to note B without re-walking the vault.
    """

    config: Any  # config.Config; untyped here to avoid a circular import
    notes: list[Note] = field(default_factory=list)

    # Lookup tables. Keys are normalised via `index_key`.
    by_slug: dict[str, Note] = field(default_factory=dict)
    by_title: dict[str, Note] = field(default_factory=dict)
    by_alias: dict[str, Note] = field(default_factory=dict)
    by_vault_path: dict[str, Note] = field(default_factory=dict)
    # Every asset in the vault, keyed by lowercased basename AND by
    # vault-relative path, so both `[[Luma.jpg]]` and `[[z_Assets/Luma.jpg]]` hit.
    assets: dict[str, Asset] = field(default_factory=dict)

    # Sidecar artifacts produced by `finalize()` modules, written by the emitter.
    generated: dict[str, Any] = field(default_factory=dict)

    # Vault paths of assets any module resolved during the run. Modules can
    # surface images that a raw scan of the source text would never find --
    # `dataview_queries`'s queries embed images taken from *other* notes' frontmatter --
    # so resolution records usage here and the emitter copies the union.
    referenced_assets: set[str] = field(default_factory=set)

    # Vault paths a module has ruled out of publication. Subtracted from the
    # copy set, so a blocked image cannot reach `public/` even though the notes
    # that used it still reference it in their untransformed source -- which is
    # where `collect_asset_references` reads from.
    blocked_assets: set[str] = field(default_factory=set)

    # Lazily built on first `find_icon()` call; untyped to avoid a circular
    # import (`icons.py` imports this module for `index_key`).
    _icons: Any = field(default=None, repr=False, compare=False)

    def published_notes(self) -> list[Note]:
        return [n for n in self.notes if n.published]

    def find_note(self, reference: str) -> Note | None:
        """Resolve a wikilink target the way Obsidian does: path, then name."""
        key = index_key(reference)
        if not key:
            return None
        for table in (self.by_vault_path, self.by_title, self.by_slug, self.by_alias):
            hit = table.get(key)
            if hit is not None:
                return hit
        return None

    def find_asset(self, reference: str) -> Asset | None:
        return self.assets.get(index_key(reference))

    def find_icon(self, reference: str) -> str | None:
        """A vendored icon-library SVG, as a data URI -- see `icons.py`."""
        if self._icons is None:
            from . import icons

            self._icons = icons.IconIndex(self.config)
        return self._icons.find(reference)


def index_key(value: str) -> str:
    """Normalise a lookup key: strip a `.md` suffix, casefold, collapse slashes."""
    text = str(value).strip().replace("\\", "/").strip("/")
    if text.lower().endswith(".md"):
        text = text[:-3]
    return text.casefold()
