"""The ```i18n-gallery fence: a note's own images, as a grid.

Two things are worth proving here, and they are different in kind.

The first is that the *resolver* is genuinely shared. `resolve_images` grew a
`key` parameter so one implementation serves both the infobox and the gallery;
if that sharing is real, then pairing, translation, `|size` and the scalar form
all work under `gallery` without a line of new code, and the cases below assert
exactly that rather than re-testing the resolver's internals.

The second is the part that is new: a layout that declares no gallery, an
image listed in both families, and the markup itself.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.vault_parser import infobox as ib
from scripts.vault_parser.model import Note
from scripts.vault_parser.modules import i18n_gallery


def table(**kwargs) -> ib.InfoboxTable:
    kwargs.setdefault("available", True)
    return ib.InfoboxTable(**kwargs)


TABLE = table(
    values={
        "galleryLabel": {"Boceto": {"ja": "下書き", "en": "Sketch"}},
        "imageLabel": {"Retrato": {"ja": "肖像"}},
    },
    layouts={
        "persona": {
            "image": "imagen",
            "imageFallback": "stand-in.svg",
            "gallery": "gallery",
            "galleryColumns": 3,
        },
        # A type with an infobox but no gallery: the fence should say so
        # rather than render an empty grid.
        "lugar": {"image": "imagen"},
    },
)
LAYOUT = TABLE.layouts["persona"]


def refs(images):
    return [i.ref for i in images]


def labels(images):
    return [i.label for i in images]


def note(name: str = "Alguien", **kwargs) -> Note:
    return Note(
        vault_path=kwargs.pop("vault_path", f"World/{name}.md"),
        source=Path(f"/vault/World/{name}.md"),
        collection="encyclopedia",
        slug=name.lower().replace(" ", "-"),
        title=kwargs.pop("title", name),
        breadcrumb=["World"],
        frontmatter=kwargs.pop("frontmatter", {}),
        tags=kwargs.pop("tags", []),
        **kwargs,
    )


class SharedResolver(unittest.TestCase):
    """The gallery reads its own key family through the infobox's resolver."""

    def test_it_reads_the_gallery_key_not_the_image_key(self):
        fm = {"imagen": ["a.webp"], "gallery": ["b.webp", "c.webp"]}
        got = ib.resolve_images(TABLE, LAYOUT, fm, "es", "es", key="gallery")
        self.assertEqual(refs(got), ["b.webp", "c.webp"])

    def test_the_infobox_still_reads_the_image_key(self):
        # The same frontmatter, the default key: the two families do not leak.
        fm = {"imagen": ["a.webp"], "gallery": ["b.webp", "c.webp"]}
        self.assertEqual(refs(ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")), ["a.webp"])

    def test_labels_pair_by_position_under_the_gallery_key(self):
        fm = {"gallery": ["b.webp", "c.webp"], "galleryLabel": ["Boceto", "Otro"]}
        got = ib.resolve_images(TABLE, LAYOUT, fm, "es", "es", key="gallery")
        self.assertEqual(labels(got), ["Boceto", "Otro"])

    def test_gallery_labels_translate_through_the_shared_vocabulary(self):
        fm = {"gallery": ["b.webp"], "galleryLabel": ["Boceto"]}
        got = ib.resolve_images(TABLE, LAYOUT, fm, "ja", "es", key="gallery")
        self.assertEqual(labels(got), ["下書き"])

    def test_a_per_note_override_still_wins(self):
        fm = {
            "gallery": ["b.webp"],
            "galleryLabel": ["Boceto"],
            "galleryLabel_ja": ["一点物"],
        }
        got = ib.resolve_images(TABLE, LAYOUT, fm, "ja", "es", key="gallery")
        self.assertEqual(labels(got), ["一点物"])

    def test_a_scalar_gallery_is_one_image(self):
        got = ib.resolve_images(TABLE, LAYOUT, {"gallery": "b.webp"}, "es", "es", key="gallery")
        self.assertEqual(refs(got), ["b.webp"])

    def test_wrapped_references_are_unwrapped_here_too(self):
        fm = {"gallery": ["[[b.webp]]", "![[c.webp]]", "[[]]"]}
        got = ib.resolve_images(TABLE, LAYOUT, fm, "es", "es", key="gallery")
        self.assertEqual(refs(got), ["b.webp", "c.webp"])

    def test_the_label_key_is_derived_from_the_family_name(self):
        self.assertEqual(ib.label_key_for({}, "gallery"), "galleryLabel")
        self.assertEqual(ib.label_key_for({}), "imageLabel")

    def test_a_layout_may_rename_the_label_key(self):
        self.assertEqual(
            ib.label_key_for({"galleryLabel": "pies"}, "gallery"), "pies"
        )


class NoFallback(unittest.TestCase):
    """An empty gallery is empty; only the infobox has a stand-in."""

    def test_the_infobox_fallback_does_not_leak_into_the_gallery(self):
        # `imageFallback` is declared and `galleryFallback` is not, so a note
        # with no gallery images gets nothing rather than a row of stand-ins.
        self.assertEqual(ib.resolve_images(TABLE, LAYOUT, {}, "es", "es", key="gallery"), [])

    def test_the_infobox_fallback_still_applies_to_the_infobox(self):
        got = ib.resolve_images(TABLE, LAYOUT, {}, "es", "es")
        self.assertEqual(refs(got), ["stand-in.svg"])

    def test_a_declared_gallery_fallback_would_be_honoured(self):
        layout = dict(LAYOUT, galleryFallback="hueco.svg")
        got = ib.resolve_images(TABLE, layout, {}, "es", "es", key="gallery")
        self.assertEqual(refs(got), ["hueco.svg"])


class Sizes(unittest.TestCase):
    def test_the_gallery_has_its_own_default_size(self):
        layout = dict(LAYOUT, imageSize="250", gallerySize="400")
        got = ib.resolve_images(TABLE, layout, {"gallery": ["b.webp"]}, "es", "es", key="gallery")
        self.assertEqual(got[0].size, "400")

    def test_an_inline_size_still_wins(self):
        layout = dict(LAYOUT, gallerySize="400")
        got = ib.resolve_images(
            TABLE, layout, {"gallery": ["b.webp|120"]}, "es", "es", key="gallery"
        )
        self.assertEqual((got[0].ref, got[0].size), ("b.webp", "120"))


class Columns(unittest.TestCase):
    """`galleryColumns` is inline because a stylesheet cannot enumerate it."""

    def test_a_declared_count_becomes_a_custom_property(self):
        self.assertEqual(
            i18n_gallery._columns(LAYOUT), ' style="--ml-gallery-columns: 3"'
        )

    def test_no_count_leaves_the_grid_to_decide(self):
        self.assertEqual(i18n_gallery._columns({"gallery": "gallery"}), "")

    def test_a_non_numeric_count_is_ignored_rather_than_written_through(self):
        # Writing `--ml-gallery-columns: muchas` through would break the whole
        # `grid-template-columns` declaration, not just the count.
        self.assertEqual(i18n_gallery._columns(dict(LAYOUT, galleryColumns="muchas")), "")

    def test_a_count_written_as_a_string_still_works(self):
        self.assertEqual(
            i18n_gallery._columns(dict(LAYOUT, galleryColumns=" 4 ")),
            ' style="--ml-gallery-columns: 4"',
        )


class FenceOptions(unittest.TestCase):
    def test_an_empty_fence_is_the_common_case(self):
        self.assertEqual(i18n_gallery._parse_options(""), {})

    def test_layout_may_be_named(self):
        self.assertEqual(i18n_gallery._parse_options("layout: persona"), {"layout": "persona"})

    def test_comments_and_blank_lines_are_skipped(self):
        self.assertEqual(i18n_gallery._parse_options("# nota\n\nlayout: persona"),
                         {"layout": "persona"})

    def test_the_query_fences_selectors_are_refused_rather_than_ignored(self):
        # `from:`/`not:` choose among *other* notes. There is nothing to choose
        # here, and silently ignoring them would look like they had worked.
        for option in ("from: algo", "not: algo"):
            with self.subTest(option=option):
                self.assertIsInstance(i18n_gallery._parse_options(option), str)

    def test_a_line_that_is_not_key_value_is_an_error(self):
        self.assertIsInstance(i18n_gallery._parse_options("solo texto"), str)


class Markup(unittest.TestCase):
    """The DOM, which the stylesheets in both repositories key off."""

    def setUp(self):
        self.module = i18n_gallery.I18nGalleryModule(enabled=True)

    def test_an_image_with_a_label_is_a_figure_with_a_caption(self):
        got = self.module._figure(note(), _Ctx(), ib.GalleryImage("b.webp", label="Boceto"), "Alguien")
        self.assertEqual(
            got,
            '<figure class="ml-gallery-item">'
            '<img src="/vault-assets/b.webp" alt="Boceto" />'
            '<figcaption class="ml-gallery-caption">Boceto</figcaption>'
            "</figure>",
        )

    def test_an_unlabelled_image_gets_no_caption_element_at_all(self):
        # Not an empty `<figcaption>`: that would reserve its padding and draw
        # a blank strip under the picture.
        got = self.module._figure(note(), _Ctx(), ib.GalleryImage("b.webp"), "Alguien")
        self.assertNotIn("figcaption", got)
        self.assertIn('alt="Alguien"', got)

    def test_the_caption_is_escaped(self):
        got = self.module._figure(
            note(), _Ctx(), ib.GalleryImage("b.webp", label="A & <B>"), "Alguien"
        )
        self.assertIn("A &amp; &lt;B&gt;", got)
        self.assertNotIn("<B>", got)

    def test_an_unresolved_image_contributes_nothing(self):
        got = self.module._figure(note(), _Ctx(resolve=False), ib.GalleryImage("x.webp"), "A")
        self.assertEqual(got, "")

    def test_a_size_becomes_width_and_height_attributes(self):
        got = self.module._figure(
            note(), _Ctx(), ib.GalleryImage("b.webp", size="300x200"), "A"
        )
        self.assertIn('width="300"', got)
        self.assertIn('height="200"', got)


class _Ctx:
    """Enough context for `linking.image_tag`.

    `find_icon` has to exist and return None: `resolve_image` consults it when
    no asset matches, and a gallery of icon references is a real possibility.
    """

    def __init__(self, resolve: bool = True) -> None:
        self._resolve = resolve
        self.referenced_assets: set[str] = set()
        self.config = _Config()

    def find_asset(self, target: str):
        return _Asset(target) if self._resolve else None

    def find_icon(self, target: str):
        return None


class _Config:
    asset_base_url = "/vault-assets"


class _Asset:
    def __init__(self, name: str) -> None:
        self.out_name = name
        self.vault_path = f"z_Assets/{name}"


if __name__ == "__main__":
    unittest.main()
