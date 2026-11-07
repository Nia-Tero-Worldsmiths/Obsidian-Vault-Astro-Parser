"""The two-pass run.

Pass 1 (`ingest`) walks and indexes everything, so that pass 2 can resolve
references between notes without a second walk. Pass 2 runs each published note
through the module pipeline and emits it.

Unpublished notes stay in the index -- `links` needs to know a link points at
a real-but-unpublished note in order to render it as plain text rather than as
a dead link.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import assets as assets_mod
from . import emit, ingest
from .config import Config
from .model import Asset, Note
from .modules.registry import Pipeline
from .report import Report

# Markdown image/link syntax: ![alt](path) and [text](path).
@dataclass
class RunResult:
    report: Report
    notes: list[Note] = field(default_factory=list)
    copied_assets: list[Asset] = field(default_factory=list)


def execute(
    config: Config,
    *,
    dry_run: bool = False,
    clean: bool = False,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    also: set[str] | None = None,
) -> RunResult:
    report = Report(config=config, dry_run=dry_run)
    pipeline = Pipeline.from_config(config, only=only, skip=skip, also=also)

    unknown = pipeline.unknown_names((only or set()) | (skip or set()) | (also or set()))
    if unknown:
        raise ValueError(f"unknown module(s): {', '.join(sorted(unknown))}")

    # --- pass 1: walk and index ---------------------------------------
    asset_index = assets_mod.build_index(config)
    ingested = ingest.ingest(config, asset_index)
    ctx = ingested.ctx

    report.record_ingest(ingested, asset_index)

    # --- pass 2: transform and emit -----------------------------------
    published = ctx.published_notes()
    for note in published:
        pipeline.run_note(note, ctx)
    pipeline.run_finalizers(ctx)

    # Scan the *untransformed* source, so this does not depend on what the
    # pipeline has already rewritten, then add whatever the modules resolved.
    referenced = set(ctx.referenced_assets)
    for note in published:
        referenced |= assets_mod.collect_asset_references(note, config)

    # Applied last, so it wins over both sources above. A module that ruled an
    # asset out of publication must be able to keep it out of `public/`, even
    # though the raw-source scan still finds the notes referencing it.
    # `referenced` holds reference strings, so each is resolved back to the
    # asset it names before comparing.
    if ctx.blocked_assets:
        referenced = {
            reference
            for reference in referenced
            if (asset := asset_index.get(reference)) is None
            or asset.vault_path not in ctx.blocked_assets
        }

    if clean:
        report.cleaned = emit.clean(config, dry_run=dry_run)

    copied, missing = assets_mod.copy_referenced(
        asset_index, referenced, config, dry_run=dry_run
    )
    # A reference the asset index never held may still resolve as a vendored
    # icon (Font Awesome, RPG Awesome) -- see `icons.py`. Those are inlined as
    # data URIs by the modules that render them, never copied, and so must not
    # be reported as missing.
    if missing:
        missing = [reference for reference in missing if ctx.find_icon(reference) is None]
    written = emit.write_notes(published, config, dry_run=dry_run)
    generated = emit.write_generated(ctx.generated, config, dry_run=dry_run)

    # Every run prunes, so output stays a pure function of the vault even when
    # a note is renamed or unpublished without a `--clean`.
    expected = set(written) | set(generated) | {
        config.assets_dir / asset.out_name for asset in copied
    }
    report.pruned = emit.prune(expected, config, dry_run=dry_run)

    report.record_emit(
        notes_written=len(written),
        assets_copied=len(copied),
        generated_written=len(generated),
        missing_assets=missing,
        module_stats=pipeline.stats(),
        pipeline=pipeline.describe(),
        notes=published,
    )

    return RunResult(report=report, notes=published, copied_assets=copied)
