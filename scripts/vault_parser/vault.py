"""Read-only access to the Obsidian vault.

Every read in this project goes through `read_text` / `read_bytes` here, and
every one of them asserts the target resolves inside the vault before opening
it in an explicitly read-only mode. There is deliberately no write path in this
module -- writing lives in `emit.py`, which in turn refuses to touch the vault.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

MARKDOWN_SUFFIX = ".md"


class VaultAccessError(Exception):
    """An attempt to read outside the vault, or to open it for writing."""


@dataclass(frozen=True)
class VaultFile:
    """A file inside the vault, located but not yet read."""

    path: Path  # absolute
    relative: str  # POSIX, vault-relative
    parts: tuple[str, ...]  # relative path split into segments

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @property
    def parent_parts(self) -> tuple[str, ...]:
        return self.parts[:-1]


def assert_inside_vault(path: Path, vault_root: Path) -> Path:
    """Resolve `path` and confirm it lies within `vault_root`.

    Resolution happens before the comparison, so symlinks, `..` segments and
    Windows short names cannot smuggle a path out of (or into) the vault.
    """
    resolved = Path(path).resolve()
    if resolved != vault_root and vault_root not in resolved.parents:
        raise VaultAccessError(f"{resolved} is not inside the vault ({vault_root})")
    return resolved


def read_text(path: Path, vault_root: Path) -> str:
    """Read a vault file as UTF-8. The only text-read entry point."""
    target = assert_inside_vault(path, vault_root)
    # Mode is hardcoded: this function cannot be talked into writing.
    with open(target, mode="r", encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read()


def read_bytes(path: Path, vault_root: Path) -> bytes:
    """Read a vault file as bytes. The only binary-read entry point."""
    target = assert_inside_vault(path, vault_root)
    with open(target, mode="rb") as handle:
        return handle.read()


def modified_at(path: Path, vault_root: Path) -> str:
    """Source mtime as an ISO-8601 UTC string, for provenance in frontmatter."""
    target = assert_inside_vault(path, vault_root)
    stamp = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
    return stamp.replace(microsecond=0).isoformat()


def is_ignored(parts: tuple[str, ...], config: Config) -> bool:
    """True if any path segment is an ignored directory or matches an ignore glob."""
    for segment in parts:
        if segment in config.ignore_dirs:
            return True
        if any(fnmatch.fnmatch(segment, pattern) for pattern in config.ignore_globs):
            return True
    return False


def walk(config: Config, *, suffixes: frozenset[str] | None = None) -> list[VaultFile]:
    """Walk the vault, skipping ignored directories.

    Results are sorted by relative path so that every run sees files in the
    same order. That ordering is what makes slug collision tiebreaks -- and
    therefore the whole output -- deterministic.
    """
    vault_root = config.vault_root
    found: list[VaultFile] = []

    for dirpath, dirnames, filenames in os.walk(vault_root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(vault_root)
        dir_parts = tuple(p for p in rel_dir.parts if p != ".")

        # Prune in place so os.walk never descends into ignored trees.
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not is_ignored(dir_parts + (name,), config)
        )
        filenames.sort()

        if is_ignored(dir_parts, config):
            continue

        for filename in filenames:
            parts = dir_parts + (filename,)
            if is_ignored(parts, config):
                continue
            suffix = Path(filename).suffix.lower()
            if suffixes is not None and suffix not in suffixes:
                continue
            found.append(
                VaultFile(
                    path=current / filename,
                    relative="/".join(parts),
                    parts=parts,
                )
            )

    found.sort(key=lambda f: f.relative)
    return found


def walk_markdown(config: Config) -> list[VaultFile]:
    return walk(config, suffixes=frozenset({MARKDOWN_SUFFIX}))


def walk_assets(config: Config) -> list[VaultFile]:
    """Walk candidate asset files.

    Unlike markdown, assets live in `z_Assets`, which `ignore_dirs` excludes
    from the note walk -- so this deliberately re-walks with that exclusion
    lifted, while still honouring `asset_exclude_dirs` (the vendored icon packs).
    """
    vault_root = config.vault_root
    asset_ignore = config.ignore_dirs - {"z_Assets"}
    excluded = tuple(prefix.casefold() for prefix in config.asset_exclude_dirs)
    found: list[VaultFile] = []

    for dirpath, dirnames, filenames in os.walk(vault_root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(vault_root)
        dir_parts = tuple(p for p in rel_dir.parts if p != ".")
        rel_posix = "/".join(dir_parts).casefold()

        if any(
            rel_posix == prefix or rel_posix.startswith(prefix + "/")
            for prefix in excluded
        ):
            dirnames[:] = []
            continue

        dirnames[:] = sorted(
            name for name in dirnames if name not in asset_ignore
        )
        filenames.sort()

        if any(segment in asset_ignore for segment in dir_parts):
            continue

        for filename in filenames:
            if Path(filename).suffix.lower() not in config.asset_extensions:
                continue
            parts = dir_parts + (filename,)
            found.append(
                VaultFile(path=current / filename, relative="/".join(parts), parts=parts)
            )

    found.sort(key=lambda f: f.relative)
    return found
