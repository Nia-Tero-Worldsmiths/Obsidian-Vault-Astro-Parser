"""Configuration loading and path resolution.

Every path the parser will ever touch is resolved here, once, and validated
against the read-only rule: no output directory may live inside the vault.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_NAME = "vault.config.yaml"
# Committed stand-in. The real file is not: it points at a vault that lives
# outside the repository, at a path particular to whoever is running this.
EXAMPLE_CONFIG_NAME = "vault.config.example.yaml"


class ConfigError(Exception):
    """Configuration is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class ModuleSetting:
    enabled: bool = False
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    project_root: Path
    vault_root: Path  # fully resolved: the symlink target
    content_dir: Path
    generated_dir: Path
    assets_dir: Path
    asset_base_url: str
    collections: dict[str, str]
    ignore_dirs: frozenset[str]
    ignore_globs: tuple[str, ...]
    default_publish: bool
    publish_hint_min_lines: int
    asset_extensions: frozenset[str]
    asset_exclude_dirs: tuple[str, ...]
    modules: dict[str, ModuleSetting]

    @property
    def output_dirs(self) -> tuple[Path, ...]:
        return (self.content_dir, self.generated_dir, self.assets_dir)

    def module(self, name: str) -> ModuleSetting:
        return self.modules.get(name, ModuleSetting())


def find_config(explicit: str | None = None) -> Path:
    """Locate the config file, walking up from cwd if not given explicitly."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path

    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate.resolve()

    # Fall back to the package's own project root (scripts/vault_parser/../..).
    fallback = Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_NAME
    if fallback.is_file():
        return fallback

    # Not committed: it names a vault path that is specific to one machine.
    raise ConfigError(
        f"no {DEFAULT_CONFIG_NAME} found in the current directory or any parent.\n"
        f"       Copy {EXAMPLE_CONFIG_NAME} to {DEFAULT_CONFIG_NAME} and set "
        f"`vault_root` to your vault."
    )


def load(explicit: str | None = None) -> Config:
    config_path = find_config(explicit)
    project_root = config_path.parent

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping")

    vault_root = _resolve_vault(raw, project_root)
    outputs = raw.get("output") or {}
    if not isinstance(outputs, dict):
        raise ConfigError("`output` must be a mapping")

    content_dir = _resolve_output(outputs, "content", project_root, vault_root)
    generated_dir = _resolve_output(outputs, "generated", project_root, vault_root)
    assets_dir = _resolve_output(outputs, "assets", project_root, vault_root)

    collections = raw.get("collections") or {}
    if not isinstance(collections, dict) or not collections:
        raise ConfigError("`collections` must be a non-empty mapping of folder -> name")

    return Config(
        project_root=project_root,
        vault_root=vault_root,
        content_dir=content_dir,
        generated_dir=generated_dir,
        assets_dir=assets_dir,
        asset_base_url="/" + str(raw.get("asset_base_url", "/vault-assets")).strip("/"),
        collections={str(k): str(v) for k, v in collections.items()},
        ignore_dirs=frozenset(str(d) for d in raw.get("ignore_dirs") or ()),
        ignore_globs=tuple(str(g) for g in raw.get("ignore_globs") or ()),
        default_publish=bool(raw.get("default_publish", False)),
        publish_hint_min_lines=int(raw.get("publish_hint_min_lines", 4)),
        asset_extensions=frozenset(
            str(e).lower() if str(e).startswith(".") else "." + str(e).lower()
            for e in raw.get("asset_extensions") or ()
        ),
        asset_exclude_dirs=tuple(
            str(d).replace("\\", "/").strip("/") for d in raw.get("asset_exclude_dirs") or ()
        ),
        modules=_parse_modules(raw.get("modules") or {}),
    )


def _resolve_vault(raw: dict, project_root: Path) -> Path:
    value = raw.get("vault_root")
    if not value:
        raise ConfigError("`vault_root` is required")

    path = Path(str(value))
    if not path.is_absolute():
        path = project_root / path

    # `.resolve()` follows the symlink, which is the point: every later
    # containment check compares against the real vault location, so no
    # amount of path trickery can route a write back into it.
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ConfigError(
            f"vault_root does not resolve to a directory: {value} -> {resolved}"
        )
    return resolved


def _resolve_output(
    outputs: dict, key: str, project_root: Path, vault_root: Path
) -> Path:
    value = outputs.get(key)
    if not value:
        raise ConfigError(f"`output.{key}` is required")

    path = Path(str(value))
    if not path.is_absolute():
        path = project_root / path

    # The directory usually does not exist yet, so resolve the nearest existing
    # ancestor and re-append the remainder; `.resolve()` on Windows is happy
    # with non-existent paths but we want the symlink-following behaviour.
    resolved = _resolve_maybe_missing(path)

    if resolved == vault_root or vault_root in resolved.parents:
        raise ConfigError(
            f"refusing to configure `output.{key}` inside the read-only vault:\n"
            f"  {resolved}\n  is inside {vault_root}"
        )
    return resolved


def _resolve_maybe_missing(path: Path) -> Path:
    existing = path
    tail: list[str] = []
    while not existing.exists() and existing.parent != existing:
        tail.append(existing.name)
        existing = existing.parent
    return existing.resolve().joinpath(*reversed(tail))


def _parse_modules(raw: dict) -> dict[str, ModuleSetting]:
    if not isinstance(raw, dict):
        raise ConfigError("`modules` must be a mapping")

    settings: dict[str, ModuleSetting] = {}
    for name, value in raw.items():
        if value is None:
            settings[str(name)] = ModuleSetting()
        elif isinstance(value, bool):
            settings[str(name)] = ModuleSetting(enabled=value)
        elif isinstance(value, dict):
            options = {k: v for k, v in value.items() if k != "enabled"}
            settings[str(name)] = ModuleSetting(
                enabled=bool(value.get("enabled", False)), options=options
            )
        else:
            raise ConfigError(
                f"`modules.{name}` must be a boolean or a mapping, got {type(value).__name__}"
            )
    return settings


def load_or_exit(explicit: str | None = None) -> Config:
    """Load config, printing a clean message and exiting on failure."""
    try:
        return load(explicit)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
