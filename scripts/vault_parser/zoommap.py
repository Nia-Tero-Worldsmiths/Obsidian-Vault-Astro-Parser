"""Reading the zoom-map plugin's data.

The plugin stores a map in three places: the ```zoommap fence in the note (which
image, which layers, zoom limits), a sibling `*.markers.json` (the pins, in
normalised 0-1 coordinates), and the plugin's own `data.json` (the icon set, as
self-contained data-URI SVGs). This module gathers all three; rendering is
Module 8's job.

Everything here reads through `vault.read_text`, including the files under
`.obsidian/` -- those are outside the note walk's `ignore_dirs`, but they are
still inside the vault and the read-only guarantee applies to them too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import vault
from .config import Config

PLUGIN_DATA = ".obsidian/plugins/zoom-map/data.json"


@dataclass
class Icon:
    key: str
    url: str  # a data: URI, already self-contained
    size: int = 24
    anchor_x: float = 12
    anchor_y: float = 12


@dataclass
class Marker:
    x: float  # normalised 0-1 across the image
    y: float
    layer: str = "default"
    link: str = ""
    icon_key: str = ""
    tooltip: str = ""


@dataclass
class MapSpec:
    """One resolved map, ready to render."""

    image_path: str  # vault-relative
    width: int
    height: int
    markers: list[Marker] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    min_zoom: float = 0.25
    max_zoom: float = 4.0
    css_width: str = "100%"
    css_height: str | None = None
    element_id: str | None = None
    warnings: list[str] = field(default_factory=list)


class ZoommapError(Exception):
    """The fence or its data files could not be read."""


def parse_fence(source: str) -> dict[str, Any]:
    """The fence body is YAML."""
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ZoommapError(f"fence is not valid YAML: {' '.join(str(exc).split())[:90]}")
    if not isinstance(data, dict):
        raise ZoommapError("fence did not parse to a mapping")
    return data


def load_icons(config: Config) -> dict[str, Icon]:
    """Read the plugin's icon set. Missing plugin data is not fatal."""
    path = config.vault_root / PLUGIN_DATA
    if not path.exists():
        return {}

    try:
        data = json.loads(vault.read_text(path, config.vault_root))
    except (json.JSONDecodeError, OSError):
        return {}

    icons: dict[str, Icon] = {}
    for entry in data.get("icons", ()):
        key = str(entry.get("key", "")).strip()
        url = entry.get("pathOrDataUrl") or ""
        if not key or not url:
            continue
        size = _number(entry.get("size"), 24)
        icons[key.casefold()] = Icon(
            key=key,
            url=url,
            size=int(size),
            anchor_x=_number(entry.get("anchorX"), size / 2),
            anchor_y=_number(entry.get("anchorY"), size / 2),
        )
    return icons


def load_markers(vault_path: str, config: Config) -> tuple[list[Marker], int, int, list[str]]:
    """Read a `*.markers.json`. Returns (markers, width, height, warnings)."""
    path = config.vault_root / vault_path
    warnings: list[str] = []
    if not path.exists():
        raise ZoommapError(f"markers file not found: {vault_path}")

    data = json.loads(vault.read_text(path, config.vault_root))
    size = data.get("size") or {}
    width = int(_number(size.get("w"), 0))
    height = int(_number(size.get("h"), 0))

    markers: list[Marker] = []
    skipped: dict[str, int] = {}
    for entry in data.get("markers", ()):
        kind = str(entry.get("type", "pin"))
        if kind != "pin":
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        markers.append(
            Marker(
                x=_number(entry.get("x"), 0.0),
                y=_number(entry.get("y"), 0.0),
                layer=str(entry.get("layer", "default")),
                link=str(entry.get("link") or "").strip(),
                icon_key=str(entry.get("iconKey") or "").strip(),
                tooltip=str(entry.get("tooltip") or "").strip(),
            )
        )

    for kind, count in sorted(skipped.items()):
        warnings.append(f"{count} unsupported marker type(s) `{kind}` skipped")

    # Features the plugin's editor supports but a read-only page does not.
    for key, label in (("drawings", "drawing"), ("overlays", "overlay"), ("textLayers", "text layer")):
        extra = data.get(key)
        if extra:
            warnings.append(f"{len(extra)} {label}(s) not rendered")

    return markers, width, height, warnings


def build(fence: dict[str, Any], config: Config) -> MapSpec:
    """Turn a parsed fence into a fully resolved `MapSpec`."""
    bases = fence.get("imageBases") or []
    if not bases:
        raise ZoommapError("fence has no `imageBases`")

    first = bases[0]
    image_path = str(first.get("path") if isinstance(first, dict) else first).strip()
    if not image_path:
        raise ZoommapError("first `imageBases` entry has no path")

    markers_path = str(fence.get("markers") or "").strip()
    markers: list[Marker] = []
    width = height = 0
    warnings: list[str] = []
    if markers_path:
        markers, width, height, warnings = load_markers(markers_path, config)

    layers = [str(layer).casefold() for layer in fence.get("markerLayers") or ()]
    if layers:
        markers = [m for m in markers if m.layer.casefold() in layers]

    return MapSpec(
        image_path=image_path,
        width=width,
        height=height,
        markers=markers,
        layers=layers,
        min_zoom=_number(fence.get("minZoom"), 0.25),
        max_zoom=_number(fence.get("maxZoom"), 4.0),
        css_width=str(fence.get("width") or "100%"),
        css_height=str(fence["height"]) if fence.get("height") else None,
        element_id=str(fence["id"]) if fence.get("id") else None,
        warnings=warnings,
    )


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
