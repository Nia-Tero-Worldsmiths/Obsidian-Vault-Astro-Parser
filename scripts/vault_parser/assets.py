"""Asset discovery and copying.

Images are indexed by both basename and vault-relative path, case-insensitively,
because notes reference them either way (`![[Luma.jpg]]` and
`[[z_Assets/Maps/Luma.jpg]]`) and the vault contains at least one uppercase
extension (`.JPG`) that a case-sensitive lookup would miss.

Only assets that are actually referenced get copied -- the vault carries ~2500
vendored icon SVGs that are not content.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter, vault
from .config import Config
from .model import Asset, Note, index_key
from .slugs import SlugRegistry, slugify

_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)<>\s]+)>?\s*\)")


@dataclass
class AssetIndex:
    """Every candidate asset in the vault, before any copying decision."""

    by_key: dict[str, Asset] = field(default_factory=dict)
    collisions: list[tuple[str, str, str]] = field(default_factory=list)

    def get(self, reference: str) -> Asset | None:
        return self.by_key.get(index_key(reference))

    def all_assets(self) -> list[Asset]:
        # Multiple keys point at the same Asset; de-duplicate by source path.
        seen: dict[str, Asset] = {}
        for asset in self.by_key.values():
            seen.setdefault(asset.vault_path, asset)
        return sorted(seen.values(), key=lambda a: a.vault_path)


def build_index(config: Config) -> AssetIndex:
    """Walk the vault and index every image, resolving basename collisions."""
    index = AssetIndex()
    registry = SlugRegistry()

    for found in vault.walk_assets(config):
        extension = found.suffix
        slug = registry.assign(found.stem, found.parts)
        asset = Asset(
            vault_path=found.relative,
            source=found.path,
            slug=slug,
            extension=extension,
        )

        # Path key is unambiguous and always registered.
        index.by_key[index_key(found.relative)] = asset

        # Basename key is how notes usually refer to images; first writer wins,
        # and because the walk is sorted that winner is stable across runs.
        name_key = index_key(found.name)
        existing = index.by_key.get(name_key)
        if existing is None:
            index.by_key[name_key] = asset
        elif existing.vault_path != asset.vault_path:
            index.collisions.append(
                (found.name, existing.vault_path, asset.vault_path)
            )

    # Registry collisions are deliberately not reported: two files sharing a
    # basename simply get distinct output names, which is correct. What matters
    # is the *reference* ambiguity recorded above -- `![[Sasa.png]]` can only
    # resolve to one of them.
    return index


def copy_referenced(
    index: AssetIndex,
    referenced: set[str],
    config: Config,
    *,
    dry_run: bool = False,
) -> tuple[list[Asset], list[str]]:
    """Copy the assets actually referenced by published notes.

    Returns the copied assets and the references that matched nothing.
    """
    resolved: dict[str, Asset] = {}
    missing: list[str] = []

    for reference in sorted(referenced):
        asset = index.get(reference)
        if asset is None:
            missing.append(reference)
        else:
            resolved.setdefault(asset.vault_path, asset)

    copied = sorted(resolved.values(), key=lambda a: a.vault_path)
    if dry_run:
        return copied, missing

    config.assets_dir.mkdir(parents=True, exist_ok=True)
    for asset in copied:
        destination = config.assets_dir / asset.out_name
        _copy(asset.source, destination, config)

    return copied, missing


def _copy(source: Path, destination: Path, config: Config) -> None:
    """Copy one asset out of the vault, reading through the read-only layer."""
    # `read_bytes` asserts the source is inside the vault; the destination is
    # already known to be outside it (validated at config load).
    data = vault.read_bytes(source, config.vault_root)
    destination.write_bytes(data)


def public_url(asset: Asset, config: Config) -> str:
    return f"{config.asset_base_url}/{asset.out_name}"


# Lives here rather than in `run` because a pipeline module needs it too, and
# `run` imports the module registry -- so a module importing `run` back would
# be a cycle. The question it answers, "which assets does this note point at",
# is this file's subject anyway.
def collect_asset_references(note: Note, config: Config) -> set[str]:
    """Every image a note points at, from frontmatter and body alike.

    Obsidian expresses image references three ways in this vault -- an `imagen`
    frontmatter value, a `![[...]]` embed, and (in at least one note) a bare
    `[[alquimia tiera.png]]` link -- plus standard markdown syntax. All four
    resolve through the same asset map.

    Scans `raw_body` rather than `body`: by the time this runs the pipeline has
    rewritten embeds into `<img>` tags, and scanning the transformed text would
    miss exactly the images the transform just committed us to serving.
    """
    references: set[str] = set()

    def consider(candidate: str) -> None:
        cleaned = candidate.strip().strip("<>").split("#", 1)[0].split("|", 1)[0].strip()
        if not cleaned or "://" in cleaned:
            return
        suffix = "." + cleaned.rsplit(".", 1)[-1].lower() if "." in cleaned else ""
        if suffix in config.asset_extensions:
            references.add(cleaned)

    for ref in note.frontmatter_refs:
        consider(ref.target)
    for ref in frontmatter.parse_links(note.raw_body):
        consider(ref.target)
    for match in _MARKDOWN_LINK.finditer(note.raw_body):
        consider(match.group(1))

    # Frontmatter values that name an image directly rather than as a wikilink,
    # e.g. `imagen: Moby (Rondfort).jpg`.
    for value in note.frontmatter.values():
        for candidate in _strings(value):
            consider(candidate)

    return references


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for entry in value for item in _strings(entry)]
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _strings(entry)]
    return []
