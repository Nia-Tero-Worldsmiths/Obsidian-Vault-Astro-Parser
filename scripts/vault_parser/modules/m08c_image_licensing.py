"""Module 8c -- Image licensing.

Enforces the vault's own image policy. `z_Assets/CREDITS.yaml` records each
image's origin and, through its `policy` table, which origins may be published.
This module holds the site to that: an image whose origin is not publishable is
replaced by a placeholder wherever it appears, and is not copied to `public/`.

It also collects the attributions the surviving images oblige, into
`src/generated/credits.json`, so a credits page can be built from data rather
than from someone remembering.

**Why this rewrites emitted markup rather than gating at the source.** Four
modules produce images -- links (2), inline dataview (3), query portraits (6)
and map bases (8) -- and each builds its `<img>` differently. Filtering at each
would be four chances to miss one, and a missed one publishes an image the
vault said not to. Rewriting the finished HTML afterwards is a single
choke point that cannot be bypassed by a module added later.

Runs at 82: after every image producer, before `empty_headings` (85), so a
section whose only content is a withheld image still counts as having content
and is not silently deleted along with it.

**Off by default.** Turning it on removes images from the live site, which is
an editorial decision, and the vault's classification work is still in
progress. Preview first:

    .venv/Scripts/python -m scripts.vault_parser --dry-run --verbose --only image_licensing

    modules:
      image_licensing:
        enabled: true
        placeholder: true       # false removes the image instead
        lenient: false          # true publishes everything anyway, just reports

`lenient` is for testing the classification work itself, without yet taking
images off the site: a non-publishable image is left exactly as the earlier
modules produced it -- no placeholder, no `blocked_assets` -- and is instead
listed in the run report and in `credits.json` under `flagged`, so someone can
see what enforcement would currently do before it actually does it.
"""

from __future__ import annotations

import html
import re

from .. import credits as credits_mod
from ..model import Note, VaultContext
from .base import TransformModule

# Every `<img>` the pipeline emits points at the asset base URL; nothing else
# does, which is what makes one pattern sufficient.
_IMG = re.compile(r"<img\b[^>]*?\bsrc=\"(?P<src>[^\"]*)\"[^>]*>", re.IGNORECASE)

# Font Awesome Free 6.4.0 `image` (CC BY 4.0, (c) 2023 Fonticons, Inc.),
# inlined so the placeholder needs no asset of its own.
_PLACEHOLDER_ICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'"
    "%3E%3Cpath d='M0 96C0 60.7 28.7 32 64 32H448c35.3 0 64 28.7 64 64V416c0 35.3-28.7"
    " 64-64 64H64c-35.3 0-64-28.7-64-64V96zM323.8 202.5c-4.5-6.6-11.9-10.5-19.8-10.5s"
    "-15.4 3.9-19.8 10.5l-87 127.6L170.7 297c-4.6-5.7-11.5-9-18.7-9s-14.2 3.3-18.7 9l"
    "-64 80c-5.8 7.2-6.9 17.1-2.9 25.4s12.4 13.6 21.6 13.6h96 32H424c8.9 0 17.1-4.9 21"
    ".2-12.8s3.6-17.4-1.4-24.7l-120-176zM112 192a48 48 0 1 0 0-96 48 48 0 1 0 0 96z'"
    "/%3E%3C/svg%3E"
)

DEFAULT_NOTICE = "Imagen no disponible por motivos de licencia"


class ImageLicensingModule(TransformModule):
    name = "image_licensing"
    order = 82
    summary = "Withhold images the vault's CREDITS.yaml does not allow publishing"
    stub = False

    ARTIFACT = "credits.json"

    def __init__(self, enabled: bool = False, options: dict | None = None) -> None:
        super().__init__(enabled=enabled, options=options)
        self._index: credits_mod.CreditsIndex | None = None
        self._use_placeholder = bool(self.options.get("placeholder", True))
        self._notice = str(self.options.get("notice", DEFAULT_NOTICE))
        self._lenient = bool(self.options.get("lenient", False))
        #: out_name -> Credit, for everything that survived.
        self._published: dict[str, credits_mod.Credit] = {}
        self._withheld: dict[str, credits_mod.Credit] = {}
        #: out_name -> Credit, non-publishable but let through by `lenient`.
        self._flagged: dict[str, credits_mod.Credit] = {}

    # -- per note --------------------------------------------------------

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        index = self._load(ctx)
        if not index.available or "<img" not in note.body:
            return note

        note.body = _IMG.sub(lambda m: self._consider(m, ctx), note.body)
        return note

    def _load(self, ctx: VaultContext) -> credits_mod.CreditsIndex:
        if self._index is None:
            self._index = credits_mod.load(ctx.config)
            for warning in self._index.warnings:
                self.warn(warning)
            self.count("credits entries", len(self._index.by_filename))
        return self._index

    def _consider(self, match: re.Match, ctx: VaultContext) -> str:
        src = match.group("src")
        asset = _asset_for(src, ctx)
        if asset is None:
            # Not one of ours -- a data: URI map pin, say. Leave it alone.
            return match.group(0)

        source_name = asset.vault_path.rsplit("/", 1)[-1]
        credit = self._index.for_asset(source_name)  # type: ignore[union-attr]

        if credit.publishable:
            self._published[asset.out_name] = credit
            self.count("images allowed")
            if credit.inferred:
                self.count("images allowed by default (no row)")
            return match.group(0)

        if self._lenient:
            self._flagged[asset.out_name] = credit
            self.count("images flagged (lenient)")
            return match.group(0)

        self._withheld[asset.out_name] = credit
        ctx.blocked_assets.add(asset.vault_path)
        self.count("images withheld")
        return self._replacement(match.group(0), credit)

    def _replacement(self, original: str, credit: credits_mod.Credit) -> str:
        if not self._use_placeholder:
            return ""

        # Keep the original alt text: it describes the subject, which is still
        # useful when the picture of it is missing.
        alt = re.search(r'\balt="([^"]*)"', original)
        label = html.escape(alt.group(1)) if alt else ""
        reason = html.escape(f"{self._notice} ({credit.origin})")

        return (
            f'<span class="image-withheld" role="img" aria-label="{label or reason}" '
            f'title="{reason}">'
            f'<img class="image-withheld-icon" src="{_PLACEHOLDER_ICON}" alt="" />'
            "</span>"
        )

    # -- report ----------------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        index = self._load(ctx)
        if not index.available:
            return

        # In lenient mode a flagged image is still shipped, so it belongs in
        # the same list a credits page would read -- just marked, so nobody
        # mistakes "not blocked yet" for "cleared".
        shipped = {**self._published, **self._flagged}
        ctx.generated[self.ARTIFACT] = {
            "images": [
                {
                    "file": out_name,
                    "source": credit.filename,
                    "origin": credit.origin,
                    "license": credit.license,
                    **({"author": credit.author} if credit.author else {}),
                    **({"sourceUrl": credit.source} if credit.source else {}),
                    **({"flagged": True} if out_name in self._flagged else {}),
                }
                for out_name, credit in sorted(shipped.items())
            ],
            "withheldCount": len(self._withheld),
            "flaggedCount": len(self._flagged),
        }

        self._report_withheld()
        self._report_flagged()
        self._report_attribution()

    def _report_withheld(self) -> None:
        if not self._withheld:
            return
        lines = [
            "These images are used by published notes but their origin is not",
            "publishable under the vault's own `policy`. They are replaced by a",
            "placeholder and are not copied to public/.",
            "",
        ]
        for out_name, credit in sorted(self._withheld.items()):
            why = "no row in CREDITS.yaml" if credit.inferred else f"origin `{credit.origin}`"
            lines.append(f"  {credit.filename}")
            lines.append(f"      {why}, licence `{credit.license}`")
        self.section("Images withheld", lines)

    def _report_flagged(self) -> None:
        if not self._flagged:
            return
        lines = [
            "`lenient` is on: these images are not publishable under the vault's",
            "own `policy`, but were left in place rather than replaced. They will",
            "be withheld once `lenient` is turned off.",
            "",
        ]
        for out_name, credit in sorted(self._flagged.items()):
            why = "no row in CREDITS.yaml" if credit.inferred else f"origin `{credit.origin}`"
            lines.append(f"  {credit.filename}")
            lines.append(f"      {why}, licence `{credit.license}`")
        self.section("Images flagged (lenient mode)", lines)

    def _report_attribution(self) -> None:
        owed = {
            name: c
            for name, c in (self._published | self._flagged).items()
            if c.needs_attribution
        }
        if not owed:
            return
        by_author: dict[str, list[str]] = {}
        for credit in owed.values():
            key = credit.author or f"licensed: {credit.license}"
            by_author.setdefault(key, []).append(credit.filename)

        lines = ["Published images that oblige an attribution:", ""]
        for author, files in sorted(by_author.items()):
            lines.append(f"  {author} -- {len(files)} image(s)")
            for name in sorted(files)[:4]:
                lines.append(f"      {name}")
            if len(files) > 4:
                lines.append(f"      ... and {len(files) - 4} more")
        self.section("Image attribution", lines)


def _asset_for(src: str, ctx: VaultContext):
    """Map an emitted src back to the vault asset it came from."""
    prefix = ctx.config.asset_base_url.rstrip("/") + "/"
    if not src.startswith(prefix):
        return None
    out_name = src[len(prefix):]
    for asset in ctx.assets.values():
        if asset.out_name == out_name:
            return asset
    return None
