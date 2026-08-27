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
class Language:
    """One language the site publishes in."""

    code: str
    label: str


@dataclass(frozen=True)
class I18nSettings:
    """Vault-wide language settings, shared by several modules and by `emit`.

    Typed at the top level rather than hidden in one module's `options`
    because three separate consumers need it: `lang_blocks` splits bodies by
    it, `i18n_infobox` resolves labels and values by it, and `emit` writes the
    per-language wrappers.

    These mirror the plugin's own defaults (`src/settings.ts`). The plugin
    keeps its copy in `.obsidian/plugins/i18n-manager/data.json`, which is
    git-ignored and absent from the vault, so the two cannot be read from one
    place today -- see `docs` note in scripts/README.md.
    """

    enabled: bool = False
    default: str = "es"
    languages: tuple[Language, ...] = ()
    #: Vault-relative path of the shared infobox translation table.
    infobox_table: str = "z_Templates/i18n-infobox.yaml"
    #: Codes the vault authors in but the site does not serve yet.
    ignore: tuple[str, ...] = ()

    @property
    def codes(self) -> tuple[str, ...]:
        """Every language the vault may be written in."""
        return tuple(language.code for language in self.languages)

    @property
    def served(self) -> tuple[str, ...]:
        """The languages actually emitted, in configured order.

        `languages` mirrors what the vault and the plugin support; `ignore`
        subtracts what the site is not publishing yet. Keeping the two apart is
        the point: a `:::lang en` block in a vault that supports English is a
        deliberate draft, while one in a vault that does not is a typo, and
        collapsing the lists would make those indistinguishable.
        """
        skip = {code.casefold() for code in self.ignore}
        return tuple(code for code in self.codes if code.casefold() not in skip)

    def is_ignored(self, code: str) -> bool:
        return code.casefold() in {c.casefold() for c in self.ignore}

    def label_for(self, code: str) -> str:
        for language in self.languages:
            if language.code.casefold() == code.casefold():
                return language.label
        return code

    def is_default(self, code: str) -> bool:
        return code.casefold() == self.default.casefold()


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
    i18n: I18nSettings = field(default_factory=I18nSettings)
    #: NoteType -> image filename, used when a note has no usable image of its
    #: own. Keys are matched with the same fold as an infobox layout key.
    fallback_images: dict[str, str] = field(default_factory=dict)

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
        i18n=_parse_i18n(raw.get("i18n") or {}),
        fallback_images=_parse_fallbacks(raw.get("fallback_images") or {}),
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


def _parse_fallbacks(raw: object) -> dict[str, str]:
    """Parse `fallback_images:`, a NoteType -> filename mapping."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("`fallback_images` must be a mapping of NoteType -> filename")
    return {str(key): str(value).strip() for key, value in raw.items() if value}


def _parse_i18n(raw: object) -> I18nSettings:
    """Parse the top-level `i18n:` block.

    Absent or `enabled: false` yields a disabled setting whose `languages` is
    empty, which every consumer reads as "monolingual" and skips.
    """
    if not raw:
        return I18nSettings()
    if not isinstance(raw, dict):
        raise ConfigError("`i18n` must be a mapping")

    languages: list[Language] = []
    for entry in raw.get("languages") or ():
        if isinstance(entry, dict):
            code = str(entry.get("code", "")).strip()
            label = str(entry.get("label", code)).strip() or code
        else:
            # Bare `- es` is accepted; the label then defaults to the code.
            code = str(entry).strip()
            label = code
        if not code:
            raise ConfigError("`i18n.languages` has an entry with no `code`")
        if any(existing.code.casefold() == code.casefold() for existing in languages):
            raise ConfigError(f"`i18n.languages` lists `{code}` twice")
        languages.append(Language(code=code, label=label))

    enabled = bool(raw.get("enabled", False))

    # The default has to name a configured language, or every note would be
    # split against a code that can never be selected and the site would come
    # out empty. Better to fail at config load than to ship blank pages.
    default = str(raw.get("default", "")).strip()
    if not default and languages:
        default = languages[0].code
    if enabled:
        if not languages:
            raise ConfigError("`i18n` is enabled but lists no `languages`")
        if not any(language.code.casefold() == default.casefold() for language in languages):
            raise ConfigError(
                f"`i18n.default` is `{default}`, which is not one of "
                f"`i18n.languages` ({', '.join(l.code for l in languages)})"
            )

    ignore = tuple(str(code).strip() for code in raw.get("ignore") or () if str(code).strip())
    known = {language.code.casefold() for language in languages}
    for code in ignore:
        if code.casefold() not in known:
            raise ConfigError(
                f"`i18n.ignore` lists `{code}`, which is not one of `i18n.languages`"
            )
    # Ignoring the default would leave every note with no language to fall back
    # on, and the site would come out empty rather than untranslated.
    if enabled and any(code.casefold() == default.casefold() for code in ignore):
        raise ConfigError(f"`i18n.ignore` lists the default language `{default}`")

    return I18nSettings(
        enabled=enabled,
        default=default or "es",
        languages=tuple(languages),
        ignore=ignore,
        infobox_table=str(
            raw.get("infobox_table", "z_Templates/i18n-infobox.yaml")
        ).replace("\\", "/").strip("/"),
    )


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
