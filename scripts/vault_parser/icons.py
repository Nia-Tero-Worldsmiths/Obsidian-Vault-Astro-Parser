"""Vendored icon-library SVGs (Font Awesome, RPG Awesome) under `asset_exclude_dirs`.

`assets.py` deliberately keeps these out of the copyable-asset index -- the
vault carries ~2500 of them and none are content. A note can still *use* one as
a placeholder image, though (`imagen: castle-flag.svg`), and when it does this
module gives it the same treatment Module 8 gives map pin icons: read once,
inlined as a self-contained data URI, never copied to `public/`.

The index of *names* is built once (a directory listing, no file contents
read), but a file's bytes are only read the first time it is actually
resolved -- there is no reason to read all ~2500 vendored files when a note
references at most a handful.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from . import vault
from .config import Config
from .model import index_key


class IconIndex:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._paths: dict[str, Path] | None = None
        self._cache: dict[str, str | None] = {}

    def find(self, reference: str) -> str | None:
        """An icon's filename (not a full vault path) to its data URI, or None."""
        key = index_key(reference)
        if key not in self._cache:
            path = self._names().get(key)
            self._cache[key] = _data_uri(path, self._config.vault_root) if path else None
        return self._cache[key]

    def _names(self) -> dict[str, Path]:
        if self._paths is None:
            self._paths = _walk(self._config)
        return self._paths


def _walk(config: Config) -> dict[str, Path]:
    found: list[Path] = []
    for rel in sorted(config.asset_exclude_dirs):
        root = config.vault_root / rel
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for filename in filenames:
                if Path(filename).suffix.lower() == ".svg":
                    found.append(Path(dirpath) / filename)

    # Sorted so a name collision across icon packs picks whichever full path
    # sorts first, deterministically -- same tiebreak as `assets.py`'s
    # basename index, and for the same reason: `os.walk` order is not.
    paths: dict[str, Path] = {}
    for path in sorted(found):
        paths.setdefault(index_key(path.name), path)
    return paths


def _data_uri(path: Path, vault_root: Path) -> str:
    data = vault.read_bytes(path, vault_root)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
