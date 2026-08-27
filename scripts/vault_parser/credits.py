"""Reading the vault's image provenance file.

`z_Assets/CREDITS.yaml` records where each image came from and, through
`policy`, whether that origin may be published. This module only reads and
resolves it; `image_licensing` decides what to do with the answer.

The file is authored by hand, so everything here is tolerant: a missing file, a
missing row, an unknown origin and a malformed entry all resolve to something
usable rather than raising. The one thing it will not do is invent permission --
anything it cannot establish resolves to "do not publish", because the file's
own default is `unknown` and the policy for `unknown` is `never`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from . import vault
from .config import Config

CREDITS_PATH = "z_Assets/CREDITS.yaml"


@dataclass(frozen=True)
class Credit:
    """What is known about one image."""

    filename: str
    origin: str
    license: str
    author: str | None = None
    source: str | None = None
    publishable: bool = False
    #: True when no row named this file and the defaults were used.
    inferred: bool = False

    @property
    def needs_attribution(self) -> bool:
        """Whether rendering this image obliges us to name someone."""
        return bool(self.author) or self.origin == "licensed"


@dataclass
class CreditsIndex:
    by_filename: dict[str, Credit]
    policy: dict[str, str]
    defaults: dict[str, Any]
    available: bool = True
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []

    def for_asset(self, filename: str) -> Credit:
        """Resolve a credit, falling back to the file's own defaults."""
        hit = self.by_filename.get(filename.casefold())
        if hit is not None:
            return hit
        origin = str(self.defaults.get("origin", "unknown"))
        return Credit(
            filename=filename,
            origin=origin,
            license=str(self.defaults.get("license", "reserved")),
            publishable=self.policy.get(origin, "never") == "publish",
            inferred=True,
        )


def load(config: Config) -> CreditsIndex:
    """Read CREDITS.yaml. A missing file yields an index that blocks nothing."""
    path = config.vault_root / CREDITS_PATH

    if not path.exists():
        # No file means the vault has not started this work. Blocking every
        # image on that basis would be worse than useless, so the index reports
        # itself unavailable and the module declines to act.
        return CreditsIndex({}, {}, {}, available=False,
                            warnings=[f"{CREDITS_PATH} not found; no image was filtered"])

    warnings: list[str] = []
    try:
        raw = yaml.safe_load(vault.read_text(path, config.vault_root)) or {}
    except yaml.YAMLError as exc:
        return CreditsIndex(
            {}, {}, {}, available=False,
            warnings=[f"{CREDITS_PATH} is not valid YAML ({_brief(exc)}); no image was filtered"],
        )

    policy = {str(k): str(v) for k, v in (raw.get("policy") or {}).items()}
    defaults = raw.get("defaults") or {}
    assets = raw.get("assets") or {}

    if not policy:
        warnings.append("`policy` is empty; every origin resolves to `never`")

    index: dict[str, Credit] = {}
    for filename, row in assets.items():
        row = row or {}
        if not isinstance(row, dict):
            warnings.append(f"entry for `{filename}` is not a mapping; using defaults")
            row = {}

        origin = str(row.get("origin", defaults.get("origin", "unknown")))
        if policy and origin not in policy:
            warnings.append(f"`{filename}` has origin `{origin}`, absent from `policy`")

        license_name = str(row.get("license", defaults.get("license", "reserved")))
        publishable = policy.get(origin, "never") == "publish"

        # `licensed` without terms or a source is an unsupported claim, not a
        # permission. The file says both fields are required for that origin.
        if origin == "licensed":
            missing = [f for f in ("license", "source") if not row.get(f)]
            if missing:
                warnings.append(
                    f"`{filename}` is `licensed` but has no {' or '.join(missing)}; "
                    "treated as not publishable"
                )
                publishable = False

        index[str(filename).casefold()] = Credit(
            filename=str(filename),
            origin=origin,
            license=license_name,
            author=_clean(row.get("author")),
            source=_clean(row.get("source")),
            publishable=publishable,
        )

    return CreditsIndex(index, policy, defaults, warnings=warnings)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _brief(exc: Exception) -> str:
    return " ".join(str(exc).split())[:100]
