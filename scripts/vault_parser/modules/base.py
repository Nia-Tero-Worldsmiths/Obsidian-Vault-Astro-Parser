"""The transform module contract.

Every iteration of this project adds one module. A module receives a `Note`
whose `body` reflects every earlier module's work, and returns it modified.
`VaultContext` gives read-only access to every other note, so cross-note
resolution never needs a second walk.

Modules that produce a single sitewide artifact rather than per-note edits
(the navigation tree, the link graph) write it in `finalize()` instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Note, VaultContext


class TransformModule:
    """Base class for pipeline modules.

    Subclasses override `transform` and/or `finalize`. The default
    implementations are no-ops, which is exactly what an unimplemented module
    needs to be -- a registered stub reports zero changes and leaves the
    pipeline's shape and ordering testable from day one.
    """

    #: Config key under `modules:` in vault.config.yaml.
    name: str = ""
    #: Pipeline position. Lower runs first.
    order: int = 0
    #: One-line description, shown by `--list-modules`.
    summary: str = ""
    #: Set False once the module actually does something.
    stub: bool = True

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        self.enabled = enabled
        self.options = options or {}
        self.stats = ModuleStats(name=self.name)

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        """Modify and return `note`. Default: unchanged."""
        return note

    def finalize(self, ctx: VaultContext) -> None:
        """Emit sitewide artifacts into `ctx.generated`. Default: nothing."""
        return None

    # -- helpers for subclasses ----------------------------------------

    def count(self, key: str, amount: int = 1) -> None:
        self.stats.counters[key] = self.stats.counters.get(key, 0) + amount

    def note_changed(self) -> None:
        self.stats.notes_changed += 1

    def warn(self, message: str) -> None:
        if message not in self.stats.warnings:
            self.stats.warnings.append(message)

    def section(self, title: str, lines: list[str]) -> None:
        """Contribute a named block to the run report.

        Modules surface their own findings this way -- `links` uses it to list
        which unpublished notes are most linked to, which is a publishing
        to-do list rather than an error.
        """
        if lines:
            self.stats.sections.append((title, lines))

    def __repr__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        kind = "stub" if self.stub else "active"
        return f"<{type(self).__name__} {self.name} {state} {kind}>"


@dataclass
class ModuleStats:
    """What a module did on this run, for the report."""

    name: str
    notes_seen: int = 0
    notes_changed: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: Module-authored report blocks: (title, lines).
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
