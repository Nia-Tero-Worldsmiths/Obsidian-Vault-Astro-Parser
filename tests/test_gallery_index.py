"""`gallery.json`: which images the catalogue holds, and where each is used.

The module's job divides into two halves that fail in different ways.

Selection is by *origin*, read from CREDITS.yaml rather than from what the
notes happen to reference — which is the whole reason the module exists, since
eighteen of this vault's drawings are credited, publishable and referenced
nowhere. Getting that wrong means a catalogue that silently omits artwork.

Registration is the half that is easy to forget and invisible in a unit test
of the data: `ctx.referenced_assets` is what the emitter copies, so an entry
that is indexed but not registered becomes a 404 on the page. It is asserted
here for that reason.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from scripts.vault_parser import credits as credits_mod
from scripts.vault_parser.model import Note
from scripts.vault_parser.modules import gallery_index


def credit(filename: str, origin: str = "original", **kwargs) -> credits_mod.Credit:
    kwargs.setdefault("license", "reserved")
    kwargs.setdefault("publishable", origin in ("original", "ai", "licensed"))
    return credits_mod.Credit(filename=filename, origin=origin, **kwargs)


class Asset:
    def __init__(self, name: str) -> None:
        self.out_name = name.lower().replace(" ", "-")
        self.vault_path = f"z_Assets/{name}"


class Config:
    asset_extensions = (".webp", ".png", ".jpg", ".svg")


class Ctx:
    """Enough context for `transform` and `finalize`.

    `find_asset` resolves anything whose name is in `known`, which is what lets
    a test say "CREDITS.yaml names a file the vault no longer holds" by simply
    leaving it out.
    """

    def __init__(self, known: set[str] | None = None) -> None:
        self.config = Config()
        self.generated: dict = {}
        self.referenced_assets: set[str] = set()
        self._known = known

    def find_asset(self, reference: str):
        name = reference.rsplit("/", 1)[-1]
        if self._known is not None and name not in self._known:
            return None
        return Asset(name)


def note(name: str, *, published: bool = True, frontmatter=None, body: str = "") -> Note:
    return Note(
        vault_path=f"World/{name}.md",
        source=Path(f"/vault/World/{name}.md"),
        collection="encyclopedia",
        slug=name.lower().replace(" ", "-"),
        title=name,
        breadcrumb=["World"],
        frontmatter=frontmatter or {},
        raw_body=body,
        published=published,
    )


def run_finalize(module, ctx, rows):
    """Drive `finalize` against a stubbed credits file."""
    index = credits_mod.CreditsIndex(
        by_filename={c.filename.casefold(): c for c in rows},
        policy={"original": "publish", "licensed": "publish", "ai": "publish"},
        defaults={"origin": "unknown", "license": "reserved"},
        available=True,
    )
    with mock.patch.object(credits_mod, "load", return_value=index):
        module.finalize(ctx)
    return ctx.generated.get(gallery_index.GalleryIndexModule.ARTIFACT)


class Origins(unittest.TestCase):
    def test_the_default_is_the_project_s_own_artwork(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        self.assertEqual(module._origins, ("original", "licensed"))

    def test_the_config_may_widen_it(self):
        module = gallery_index.GalleryIndexModule(
            enabled=True, options={"origins": ["original", "ai"]}
        )
        self.assertEqual(module._origins, ("original", "ai"))

    def test_ai_is_excluded_by_default_even_though_it_publishes(self):
        # The vault's policy publishes `ai`; a catalogue of the project's own
        # artwork still should not show it. Publishable and wanted differ.
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        got = run_finalize(
            module, ctx,
            [credit("Mano.webp"), credit("Generada.webp", origin="ai")],
        )
        self.assertEqual([i["name"] for i in got["images"]], ["Mano"])

    def test_external_and_unknown_never_appear(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        got = run_finalize(
            module, Ctx(),
            [
                credit("Ajena.webp", origin="external"),
                credit("Misterio.webp", origin="unknown"),
                credit("Mano.webp"),
            ],
        )
        self.assertEqual([i["name"] for i in got["images"]], ["Mano"])


class Registration(unittest.TestCase):
    """The half that is invisible in the data and fatal on the page."""

    def test_every_indexed_image_is_registered_for_copying(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        run_finalize(module, ctx, [credit("Mano.webp"), credit("Pie.webp")])
        self.assertEqual(
            ctx.referenced_assets, {"z_Assets/Mano.webp", "z_Assets/Pie.webp"}
        )

    def test_an_excluded_image_is_not_registered(self):
        # Registering it would copy a file nothing links to.
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        run_finalize(module, ctx, [credit("Ajena.webp", origin="external")])
        self.assertEqual(ctx.referenced_assets, set())


class UsedBy(unittest.TestCase):
    def test_a_published_note_that_shows_the_image_is_recorded(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        module.transform(note("Mara", frontmatter={"imagen": "Mano.webp"}), ctx)
        got = run_finalize(module, ctx, [credit("Mano.webp")])
        self.assertEqual(
            got["images"][0]["usedBy"], [{"title": "Mara", "slug": "mara"}]
        )

    def test_an_unpublished_note_is_not_a_usage(self):
        # It is not a link target, so recording it would offer the reader a
        # link to a page that does not exist.
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        module.transform(
            note("Mara", published=False, frontmatter={"imagen": "Mano.webp"}), ctx
        )
        got = run_finalize(module, ctx, [credit("Mano.webp")])
        self.assertEqual(got["images"][0]["usedBy"], [])

    def test_an_image_no_note_shows_is_still_in_the_catalogue(self):
        # The reason the module reads CREDITS.yaml rather than the note walk.
        module = gallery_index.GalleryIndexModule(enabled=True)
        got = run_finalize(module, Ctx(), [credit("Huerfana.webp")])
        self.assertEqual(len(got["images"]), 1)
        self.assertEqual(got["images"][0]["usedBy"], [])

    def test_two_notes_showing_one_image_are_both_recorded_once(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        for name in ("Mara", "Nuria", "Mara"):
            module.transform(note(name, frontmatter={"imagen": "Mano.webp"}), ctx)
        got = run_finalize(module, ctx, [credit("Mano.webp")])
        self.assertEqual(
            [u["title"] for u in got["images"][0]["usedBy"]], ["Mara", "Nuria"]
        )

    def test_a_body_embed_counts_as_a_usage_too(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        module.transform(note("Mara", body="![[Mano.webp]]"), ctx)
        got = run_finalize(module, ctx, [credit("Mano.webp")])
        self.assertEqual(len(got["images"][0]["usedBy"]), 1)


class Shape(unittest.TestCase):
    def test_the_name_is_the_source_stem_without_its_extension(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        got = run_finalize(module, Ctx(), [credit("251112 Brunhilda.webp")])
        self.assertEqual(got["images"][0]["name"], "251112 Brunhilda")
        self.assertEqual(got["images"][0]["file"], "251112-brunhilda.webp")

    def test_optional_fields_are_omitted_rather_than_null(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        got = run_finalize(module, Ctx(), [credit("Mano.webp")])
        self.assertNotIn("author", got["images"][0])
        self.assertNotIn("sourceUrl", got["images"][0])

    def test_an_author_is_carried_through(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        got = run_finalize(module, Ctx(), [credit("Mano.webp", author="Rei-pyo")])
        self.assertEqual(got["images"][0]["author"], "Rei-pyo")

    def test_the_list_is_sorted_so_a_rerun_produces_no_diff(self):
        # `emit` sorts dict keys but not lists; ordering is this module's job.
        module = gallery_index.GalleryIndexModule(enabled=True)
        got = run_finalize(
            module, Ctx(),
            [credit("Zeta.webp"), credit("Alfa.webp"), credit("Mu.webp")],
        )
        files = [i["file"] for i in got["images"]]
        self.assertEqual(files, sorted(files))

    def test_a_row_whose_file_is_gone_is_skipped_rather_than_linked(self):
        module = gallery_index.GalleryIndexModule(enabled=True)
        got = run_finalize(
            module, Ctx(known={"Mano.webp"}),
            [credit("Mano.webp"), credit("Renombrada.webp")],
        )
        self.assertEqual([i["name"] for i in got["images"]], ["Mano"])


class NoCreditsFile(unittest.TestCase):
    def test_nothing_is_emitted_without_a_credits_file(self):
        # No origin to select on. Guessing would mean publishing images on no
        # authority at all, which is the opposite of what CREDITS.yaml is for.
        module = gallery_index.GalleryIndexModule(enabled=True)
        ctx = Ctx()
        index = credits_mod.CreditsIndex(
            by_filename={}, policy={}, defaults={}, available=False
        )
        with mock.patch.object(credits_mod, "load", return_value=index):
            module.finalize(ctx)
        self.assertNotIn(gallery_index.GalleryIndexModule.ARTIFACT, ctx.generated)
        self.assertEqual(ctx.referenced_assets, set())


if __name__ == "__main__":
    unittest.main()
