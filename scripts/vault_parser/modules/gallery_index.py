"""`gallery.json`: every image the site is allowed to show, and where it is used.

`credits.json` already exists and is nearly this, but it answers a different
question. It lists what the build *shipped* -- the images a published note
happened to reference -- which is the right answer for an attribution footer
and the wrong one for a catalogue. Eighteen of this vault's original drawings
are credited, publishable, and referenced by nothing, so they were invisible:
not on the site, and not in the list of what the site holds.

So this selects by origin instead, from the credits file rather than from the
note walk, and then *registers* what it selected. Registration is the load-
bearing half: `ctx.referenced_assets` is what the emitter copies, so without
it the page would link to files that were never written. `image_licensing`
uses the same hook for the stand-in portraits it substitutes.

Origins, not a publishable flag, because the two differ in a way that matters
editorially. `ai` images are publishable under the vault's own policy and are
still not wanted in a gallery of the project's artwork; `external` is excluded
anyway. The config says which, so widening it later is a line of YAML.

The images are copied byte for byte, at whatever size the vault holds them.
Normalising them is the plugin's job and happens in the vault, before the
parser ever runs -- there is deliberately no resizing step here.
"""

from __future__ import annotations

from typing import Any

from .. import assets as assets_mod, credits as credits_mod
from ..model import Note, VaultContext
from .base import TransformModule

#: Origins a gallery shows unless the config says otherwise. `ai` is
#: publishable under the vault's policy and deliberately left out: this is a
#: catalogue of the project's artwork.
DEFAULT_ORIGINS = ("original", "licensed")


class GalleryIndexModule(TransformModule):
    name = "gallery_index"
    # After `image_licensing` (82), so the publish decision and the withheld
    # set are already settled and this cannot disagree with them.
    order = 84
    summary = "Index every publishable image, with its author and where it is used"
    stub = False

    ARTIFACT = "gallery.json"

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        raw = self.options.get("origins")
        self._origins = tuple(
            str(o).strip().casefold() for o in raw if str(o).strip()
        ) if isinstance(raw, list) else DEFAULT_ORIGINS
        #: out_name -> [(title, slug)] of published notes using the image.
        self._usage: dict[str, list[tuple[str, str]]] = {}

    # -- per note --------------------------------------------------------

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        """Record which images this note points at.

        Done per note rather than in `finalize` because the answer is about
        one note's own source, and `collect_asset_references` is the function
        the pipeline itself uses to decide what to copy -- so the catalogue's
        idea of "used" cannot drift from the build's.
        """
        if not note.published:
            # An unpublished note is not a link target, so recording it would
            # produce a usage nobody can follow.
            return note

        for reference in assets_mod.collect_asset_references(note, ctx.config):
            asset = ctx.find_asset(reference)
            if asset is not None:
                self._usage.setdefault(asset.out_name, []).append((note.title, note.slug))
        return note

    # -- the artifact ----------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        index = credits_mod.load(ctx.config)
        if not index.available:
            # Without the credits file there is no origin to select on, and
            # guessing would mean publishing images on no authority at all.
            self.warn("no CREDITS.yaml; the gallery index was not built")
            return

        images: list[dict[str, Any]] = []
        missing: list[str] = []

        for credit in index.by_filename.values():
            if credit.origin.casefold() not in self._origins:
                continue
            asset = ctx.find_asset(credit.filename)
            if asset is None:
                # A row whose file is gone. Harmless to the build and worth
                # saying, because it usually means a rename lost its row.
                missing.append(credit.filename)
                continue

            # The hook that gets an unreferenced image copied at all.
            ctx.referenced_assets.add(asset.vault_path)

            used = sorted(set(self._usage.get(asset.out_name, [])))
            images.append(
                {
                    "file": asset.out_name,
                    # The source stem: what the author named the picture,
                    # without the extension, which is what the page shows.
                    "name": credit.filename.rsplit(".", 1)[0],
                    "origin": credit.origin,
                    "license": credit.license,
                    **({"author": credit.author} if credit.author else {}),
                    **({"sourceUrl": credit.source} if credit.source else {}),
                    "usedBy": [{"title": title, "slug": slug} for title, slug in used],
                }
            )

        # `emit` sorts dict keys but not lists, and a re-run producing a
        # different order would show up as a diff on every build.
        images.sort(key=lambda entry: entry["file"])
        ctx.generated[self.ARTIFACT] = {"images": images}

        self.count("gallery images indexed", len(images))
        self.count("gallery images used nowhere", sum(1 for i in images if not i["usedBy"]))
        self._report(images, missing)

    def _report(self, images: list[dict[str, Any]], missing: list[str]) -> None:
        unused = [i["name"] for i in images if not i["usedBy"]]
        if unused:
            self.section(
                "Gallery index: images used by no note",
                [
                    "These are published to the gallery page but no note shows",
                    "them. That is allowed -- the page exists partly to surface",
                    "them -- but a character's art usually belongs on their note.",
                    "",
                    *(f"  {name}" for name in sorted(unused)[:15]),
                ],
            )
        if missing:
            self.section(
                "Gallery index: rows with no file",
                [
                    "CREDITS.yaml names these, and the vault no longer holds",
                    "them. Usually a rename that left its row behind.",
                    "",
                    *(f"  {name}" for name in sorted(missing)[:15]),
                ],
            )
