"""Asset discovery and copying.

Images are indexed by both basename and vault-relative path, case-insensitively,
because notes reference them either way (`![[Luma.jpg]]` and
`[[z_Assets/Maps/Luma.jpg]]`) and the vault contains at least one uppercase
extension (`.JPG`) that a case-sensitive lookup would miss.

Only assets that are actually referenced get copied -- the vault carries ~2500
vendored icon SVGs that are not content.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import vault
from .config import Config
from .model import Asset, index_key
from .slugs import SlugRegistry, slugify


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
