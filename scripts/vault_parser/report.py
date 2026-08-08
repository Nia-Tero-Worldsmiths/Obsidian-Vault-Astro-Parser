"""Run reporting.

The dry-run report is the primary review surface for every future module: it
should make it obvious what the parser saw, what it emitted, and what it could
not resolve, without needing to diff the output.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .model import Note

_RULE = "-" * 62


@dataclass
class Report:
    config: Config
    dry_run: bool

    total_markdown: int = 0
    uncollected: list[str] = field(default_factory=list)
    unpublished: list[str] = field(default_factory=list)
    slug_collisions: list[tuple[str, str, str]] = field(default_factory=list)
    asset_collisions: list[tuple[str, str, str]] = field(default_factory=list)
    assets_found: int = 0
    publish_key_gaps: list[Any] = field(default_factory=list)

    notes_written: int = 0
    assets_copied: int = 0
    generated_written: int = 0
    missing_assets: list[str] = field(default_factory=list)
    cleaned: list[Path] = field(default_factory=list)
    pruned: list[Path] = field(default_factory=list)

    per_collection: Counter = field(default_factory=Counter)
    note_warnings: list[tuple[str, str]] = field(default_factory=list)
    module_stats: list[Any] = field(default_factory=list)
    pipeline: list[tuple[str, bool, bool, str]] = field(default_factory=list)

    def record_ingest(self, ingested: Any, asset_index: Any) -> None:
        ctx = ingested.ctx
        self.total_markdown = len(ctx.notes) + len(ingested.skipped_uncollected)
        self.uncollected = list(ingested.skipped_uncollected)
        self.unpublished = list(ingested.skipped_unpublished)
        self.slug_collisions = list(ingested.slug_collisions)
        self.asset_collisions = list(asset_index.collisions)
        self.assets_found = len(asset_index.all_assets())
        self.publish_key_gaps = list(ingested.publish_key_gaps)

    def record_emit(
        self,
        *,
        notes_written: int,
        assets_copied: int,
        generated_written: int,
        missing_assets: list[str],
        module_stats: list[Any],
        pipeline: list[tuple[str, bool, bool, str]],
        notes: list[Note],
    ) -> None:
        self.notes_written = notes_written
        self.assets_copied = assets_copied
        self.generated_written = generated_written
        self.missing_assets = list(missing_assets)
        self.module_stats = module_stats
        self.pipeline = pipeline
        self.per_collection = Counter(note.collection for note in notes)
        self.note_warnings = [
            (note.vault_path, warning) for note in notes for warning in note.warnings
        ]

    # -- rendering ------------------------------------------------------

    def render(self, *, verbose: bool = False) -> str:
        lines: list[str] = []
        mode = "DRY RUN -- nothing written" if self.dry_run else "WRITE"
        lines.append(_RULE)
        lines.append(f"vault-parser  [{mode}]")
        lines.append(_RULE)
        lines.append(f"  vault    {self.config.vault_root}")
        lines.append(f"  content  {self._rel(self.config.content_dir)}")
        lines.append(f"  assets   {self._rel(self.config.assets_dir)}")
        lines.append("")

        gate = "publish: true required" if not self.config.default_publish else "opt-out"
        lines.append(f"Notes  ({gate})")
        lines.append(f"  markdown found        {self.total_markdown}")
        lines.append(f"  outside collections   {len(self.uncollected)}")
        lines.append(f"  unpublished, skipped  {len(self.unpublished)}")
        lines.append(f"  published, emitted    {self.notes_written}")
        for collection, count in sorted(self.per_collection.items()):
            lines.append(f"      {collection:<18} {count}")
        lines.append("")

        lines.append("Assets")
        lines.append(f"  discovered in vault   {self.assets_found}")
        lines.append(f"  referenced, copied    {self.assets_copied}")
        lines.append(f"  referenced, missing   {len(self.missing_assets)}")
        if self.generated_written:
            lines.append(f"  generated artifacts   {self.generated_written}")
        if self.pruned:
            lines.append(f"  stale output pruned   {len(self.pruned)}")
        lines.append("")

        lines.extend(self._pipeline_section())

        problems = self._problem_sections(verbose=verbose)
        if problems:
            lines.append("")
            lines.extend(problems)

        gaps = self._publish_gap_section(verbose=verbose)
        if gaps:
            lines.append("")
            lines.extend(gaps)

        for title, body in self._module_sections():
            lines.append("")
            lines.append(title)
            lines.extend(body)

        lines.append(_RULE)
        return "\n".join(lines)

    def _publish_gap_section(self, *, verbose: bool) -> list[str]:
        """Notes with content whose frontmatter never decides `publish`."""
        if not self.publish_key_gaps:
            return []

        minimum = self.config.publish_hint_min_lines
        # Sort by real prose first: a note with 20 template lines and no prose
        # is far less likely to be publishable than one with 5 written lines.
        gaps = sorted(
            self.publish_key_gaps,
            key=lambda g: (-g.prose_lines, -g.body_lines, g.vault_path),
        )
        empty_key = sum(1 for gap in gaps if gap.key_present_but_empty)
        scaffolding = sum(1 for gap in gaps if gap.prose_lines == 0)

        lines = [
            f"No `publish` key ({len(gaps)})",
            "These notes have frontmatter and body content but never state",
            f"`publish` either way, so the gate leaves them out. Notes under",
            f"{minimum} non-blank body lines, and notes with no frontmatter, are",
            "excluded as unstarted stubs.",
            "",
            "`prose` counts authored lines only -- infobox rows, headings, tags",
            "and fenced blocks come from the template, not the author.",
            "",
        ]
        if empty_key:
            lines.insert(
                5, f"{empty_key} have an empty `publish:` rather than no key at all."
            )

        shown = gaps if verbose else gaps[:25]
        for gap in shown:
            marker = " (empty `publish:`)" if gap.key_present_but_empty else ""
            flag = "  <- template only" if gap.prose_lines == 0 else ""
            lines.append(
                f"  {gap.prose_lines:3} prose /{gap.body_lines:3} lines  "
                f"{gap.title}{marker}{flag}"
            )
            lines.append(f"        {gap.vault_path}")

        if scaffolding:
            lines.append("")
            lines.append(
                f"  {scaffolding} of {len(gaps)} have no authored prose at all -- "
                "raise `publish_hint_min_lines` or ignore them."
            )

        if len(shown) < len(gaps):
            lines.append("")
            lines.append(f"  ... and {len(gaps) - len(shown)} more (--verbose for all)")
        return lines

    def _module_sections(self) -> list[tuple[str, list[str]]]:
        """Report blocks contributed by modules via `TransformModule.section`."""
        return [
            (title, body) for stat in self.module_stats for title, body in stat.sections
        ]

    def _pipeline_section(self) -> list[str]:
        lines = ["Pipeline"]
        stats_by_name = {stat.name: stat for stat in self.module_stats}

        for name, enabled, is_stub, summary in self.pipeline:
            if not enabled:
                mark, detail = "  off", summary
            elif is_stub:
                mark, detail = "  STUB", f"{summary} (no-op)"
            else:
                stat = stats_by_name.get(name)
                changed = stat.notes_changed if stat else 0
                mark, detail = "  on", f"{summary} -- {changed} note(s) changed"
            lines.append(f"  {mark:<6} {name:<18} {detail}")

        active_stubs = [n for n, e, s, _ in self.pipeline if e and s]
        if active_stubs:
            lines.append("")
            lines.append(
                f"  note: {len(active_stubs)} enabled module(s) are still stubs "
                "and changed nothing."
            )
        return lines

    def _problem_sections(self, *, verbose: bool) -> list[str]:
        lines: list[str] = []
        limit = None if verbose else 10

        if self.slug_collisions:
            lines.append(f"Slug collisions ({len(self.slug_collisions)})")
            for base, resolved, path in self._limit(self.slug_collisions, limit):
                lines.append(f"  {base} -> {resolved}")
                lines.append(f"      {path}")
            lines.append("")

        if self.asset_collisions:
            lines.append(f"Asset name collisions ({len(self.asset_collisions)})")
            for name, first, second in self._limit(self.asset_collisions, limit):
                lines.append(f"  {name}")
                lines.append(f"      kept  {first}")
                lines.append(f"      also  {second}")
            lines.append("")

        if self.missing_assets:
            lines.append(f"Unresolved asset references ({len(self.missing_assets)})")
            for reference in self._limit(self.missing_assets, limit):
                lines.append(f"  {reference}")
            lines.append("")

        if self.note_warnings:
            lines.append(f"Note warnings ({len(self.note_warnings)})")
            for path, warning in self._limit(self.note_warnings, limit):
                lines.append(f"  {path}")
                lines.append(f"      {warning}")
            lines.append("")

        if lines and not verbose:
            lines.append("  (use --verbose for full lists)")
        return [line for line in lines if line != ""] or []

    @staticmethod
    def _limit(items: list, limit: int | None) -> list:
        return items if limit is None else items[:limit]

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.config.project_root))
        except ValueError:
            return str(path)

    def print(self, *, verbose: bool = False, stream=sys.stdout) -> None:
        print(self.render(verbose=verbose), file=stream)
