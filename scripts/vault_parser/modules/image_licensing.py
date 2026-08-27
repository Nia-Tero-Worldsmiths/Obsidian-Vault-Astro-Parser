"""Image licensing.

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
from .. import linking
from ..infobox import normalize_type_key
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
        #: NoteType (folded) -> image filename, from `fallback_images:`.
        self._fallbacks: dict[str, str] = {}
        #: out_name -> NoteType of the note that claims the image.
        self._owners: dict[str, str] | None = None
        #: Fallback filenames already checked against the policy.
        self._fallback_ok: dict[str, bool] = {}
        self._substituted = 0
        #: out_name -> Credit, non-publishable but let through by `lenient`.
        self._flagged: dict[str, credits_mod.Credit] = {}

    # -- per note --------------------------------------------------------

    def transform(self, note: Note, ctx: VaultContext) -> Note:
        index = self._load(ctx)
        if not index.available or "<img" not in note.body:
            return note

        if not self._fallbacks:
            self._fallbacks = {
                normalize_type_key(key): value
                for key, value in ctx.config.fallback_images.items()
            }

        note.body = _IMG.sub(lambda m: self._consider(m, note, ctx), note.body)
        return note

    def _load(self, ctx: VaultContext) -> credits_mod.CreditsIndex:
        if self._index is None:
            self._index = credits_mod.load(ctx.config)
            for warning in self._index.warnings:
                self.warn(warning)
            self.count("credits entries", len(self._index.by_filename))
        return self._index

    def _consider(self, match: re.Match, note: Note, ctx: VaultContext) -> str:
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
        return self._replacement(match.group(0), src, credit, note, ctx)

    def _replacement(
        self,
        original: str,
        src: str,
        credit: credits_mod.Credit,
        note: Note,
        ctx: VaultContext,
    ) -> str:
        if not self._use_placeholder:
            return ""

        stand_in = self._fallback_img(original, src, note, ctx, credit)
        if stand_in:
            return stand_in

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

    def _subject_type(self, src: str, note: Note, ctx: VaultContext) -> str:
        """The NoteType of whoever the picture is *of*.

        Usually the note being rendered, but not always: a party roster is a
        `organizacion` note whose table is a list of `persona` rows, and the
        portrait in each row belongs to the member, not to the party. Keying
        the stand-in on the containing note would leave exactly those rows
        showing the generic icon.

        Falls back to the containing note when nothing claims the image, which
        covers a body embed and any picture that is not a portrait.
        """
        if self._owners is None:
            self._owners = {}
            for candidate in ctx.notes:
                note_type = normalize_type_key(candidate.frontmatter.get("NoteType"))
                if not note_type:
                    continue
                for key in ("imagen", "image"):
                    ref = candidate.frontmatter.get(key)
                    if isinstance(ref, str) and ref.strip():
                        asset = ctx.find_asset(_bare(ref))
                        if asset is not None:
                            self._owners.setdefault(asset.out_name, note_type)

        prefix = ctx.config.asset_base_url.rstrip("/") + "/"
        out_name = src[len(prefix):] if src.startswith(prefix) else ""
        return self._owners.get(out_name) or normalize_type_key(
            note.frontmatter.get("NoteType")
        )

    def _fallback_img(
        self,
        original: str,
        match_src: str,
        note: Note,
        ctx: VaultContext,
        credit: credits_mod.Credit,
    ) -> str:
        """A NoteType's stand-in portrait, or "" to use the generic icon.

        A person with no publishable likeness still reads better with a
        neutral avatar than with a "broken image" glyph, so `fallback_images:`
        names one per NoteType.

        Two things this must not do. It must not publish an image the policy
        blocks -- the stand-in is checked against `CREDITS.yaml` like anything
        else, and a non-publishable one is refused rather than used. And it
        must not pretend to be a likeness: the original `alt` and the "why"
        tooltip are both kept, so a reader still learns the real picture is
        missing.
        """
        wanted = self._fallbacks.get(self._subject_type(match_src, note, ctx))
        if not wanted:
            return ""

        resolved = linking.resolve_image(wanted, ctx)
        if resolved is None:
            self.warn(f"fallback image not found in the vault: {wanted}")
            return ""
        url, asset = resolved

        # The stand-in has to clear the same bar as the picture it replaces.
        # Otherwise a later edit to CREDITS.yaml turns this into a hole in the
        # policy that nothing would report.
        if asset is not None:
            if not self._fallback_allowed(asset, wanted):
                return ""
            # Nothing in any note's raw body mentions this file, so the asset
            # collector would never see it and the <img> would 404.
            ctx.referenced_assets.add(asset.vault_path)

        alt = re.search(r'\balt="([^"]*)"', original)
        label = alt.group(1) if alt else html.escape(note.title)
        reason = html.escape(f"{self._notice} ({credit.origin})")

        self._substituted += 1
        self.count("portraits replaced by a fallback")
        return (
            f'<img class="image-fallback" src="{html.escape(url)}" alt="{label}" '
            f'title="{reason}" />'
        )

    def _fallback_allowed(self, asset, wanted: str) -> bool:
        cached = self._fallback_ok.get(wanted)
        if cached is not None:
            return cached

        source_name = asset.vault_path.rsplit("/", 1)[-1]
        credit = self._index.for_asset(source_name)  # type: ignore[union-attr]
        if not credit.publishable:
            why = "no row in CREDITS.yaml" if credit.inferred else f"origin `{credit.origin}`"
            self.warn(
                f"fallback image `{wanted}` is not publishable ({why}); "
                "using the generic placeholder instead"
            )
        else:
            self._published[asset.out_name] = credit
        self._fallback_ok[wanted] = credit.publishable
        return credit.publishable

    # -- report ----------------------------------------------------------

    def finalize(self, ctx: VaultContext) -> None:
        index = self._load(ctx)
        if not index.available:
            self._report_unavailable(index)
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

        self._report_substituted()
        self._report_withheld()
        self._report_flagged()
        self._report_attribution()

    def _report_unavailable(self, index: credits_mod.CreditsIndex) -> None:
        """Say loudly that nothing was filtered, and why.

        `self.warn()` is not enough: `ModuleStats.warnings` are collected but
        never rendered, which is exactly how this went unnoticed. A section is.

        The distinction that matters: a *missing* CREDITS.yaml means the vault
        has not started classifying its images, and filtering everything on
        that basis would be worse than useless. A *malformed* one means the
        policy exists and could not be read -- so every image ships, including
        the ones the file says never to publish. That is the dangerous case,
        and the one worth shouting about.
        """
        lines = [
            "!! No image was filtered on this run. Every referenced image was",
            "!! copied to public/, including any the vault marks as not",
            "!! publishable.",
            "",
        ]
        lines += [f"  {warning}" for warning in index.warnings]
        lines += [
            "",
            "A missing CREDITS.yaml is expected before that work starts. A",
            "malformed one is not: fix the file and re-run before deploying.",
        ]
        self.section("Image licensing INACTIVE", lines)

    def _report_substituted(self) -> None:
        if not self._substituted:
            return
        self.section(
            "Portrait fallbacks used",
            [
                f"{self._substituted} withheld image(s) were replaced by the",
                "stand-in their NoteType names in `fallback_images:`, rather",
                "than by the generic icon. The originals are still withheld and",
                "still listed below.",
            ],
        )

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


def _bare(ref: str) -> str:
    """Reduce an `imagen` value to a plain filename."""
    inner = ref.strip()
    if inner.startswith("!"):
        inner = inner[1:]
    inner = inner.removeprefix("[[").removesuffix("]]").strip()
    return inner.split("|", 1)[0].strip()
