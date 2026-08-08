"""Slug generation with a deterministic collision registry.

URLs are flat (`/region-de-tambler`), matching how Obsidian wikilinks resolve
by name rather than by path. That makes collisions possible, so the registry
resolves them by walking up the source path for a disambiguating segment --
never by appending a counter, which would reshuffle URLs whenever a note is
added or renamed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Characters Esperanto-style names rely on that NFKD does not decompose.
_TRANSLITERATIONS = {
    "ŝ": "s", "ĝ": "g", "ĉ": "c", "ĵ": "j", "ĥ": "h", "ŭ": "u",
    "ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "đ": "d", "ł": "l", "þ": "th",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """`Región de Tambler` -> `region-de-tambler`, `Gah'Las` -> `gah-las`."""
    text = str(value).strip().lower()
    text = "".join(_TRANSLITERATIONS.get(char, char) for char in text)
    # NFKD splits accented letters into base + combining mark; drop the marks.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _NON_SLUG.sub("-", text).strip("-")
    return text or "untitled"


_DIGITS = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple:
    """Sort key that compares digit runs numerically: `2 - Foo` before `10 - Bar`.

    Shared by the query module and the navigation tree so a chapter list and
    the sidebar order the same notes the same way.
    """
    return tuple(
        (1, int(part), "") if part.isdigit() else (0, 0, part)
        for part in _DIGITS.split(text.casefold())
        if part != ""
    )


@dataclass
class SlugRegistry:
    """Assigns unique slugs, remembering every collision for the report."""

    _taken: dict[str, str] = field(default_factory=dict)  # slug -> owning path
    collisions: list[tuple[str, str, str]] = field(default_factory=list)

    def assign(self, name: str, path_parts: tuple[str, ...]) -> str:
        """Claim a slug for `name`, disambiguating against `path_parts` if needed.

        `path_parts` is the vault-relative path split into segments, used
        deepest-first for suffixes. Callers must iterate in sorted path order so
        that whichever note claims the bare slug is stable across runs.
        """
        base = slugify(name)
        if base not in self._taken:
            self._taken[base] = "/".join(path_parts)
            return base

        # Walk up the folder chain looking for a segment that disambiguates.
        # `.../Faelas/Ubicaciones/Caimos.md` -> `caimos-ubicaciones`, then
        # `caimos-ubicaciones-faelas`, and so on.
        folders = [segment for segment in path_parts[:-1]]
        suffix_parts: list[str] = []
        for segment in reversed(folders):
            segment_slug = slugify(segment)
            if not segment_slug or segment_slug in suffix_parts:
                continue
            suffix_parts.append(segment_slug)
            candidate = f"{base}-{'-'.join(suffix_parts)}"
            if candidate not in self._taken:
                self._record_collision(base, candidate, path_parts)
                self._taken[candidate] = "/".join(path_parts)
                return candidate

        # Exhausted the path: fall back to a numeric suffix. Deterministic
        # because assignment order is deterministic.
        index = 2
        while f"{base}-{index}" in self._taken:
            index += 1
        candidate = f"{base}-{index}"
        self._record_collision(base, candidate, path_parts)
        self._taken[candidate] = "/".join(path_parts)
        return candidate

    def _record_collision(
        self, base: str, resolved: str, path_parts: tuple[str, ...]
    ) -> None:
        self.collisions.append((base, resolved, "/".join(path_parts)))

    def owner(self, slug: str) -> str | None:
        return self._taken.get(slug)

    def __contains__(self, slug: str) -> bool:
        return slug in self._taken
