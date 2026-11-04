"""Resolving an infobox's images as a labelled gallery.

Images and labels are two parallel lists paired by position. The shape was
chosen so a label can be translated on its own (`imageLabel_ja`) without
restating the filenames it belongs to — so most of what matters here is that
the pairing holds and that translation goes through the machinery the infobox
already had, rather than anything new.
"""

from __future__ import annotations

import unittest

from scripts.vault_parser import infobox as ib

NL = chr(10)


def table(**kwargs) -> ib.InfoboxTable:
    kwargs.setdefault("available", True)
    return ib.InfoboxTable(**kwargs)


TABLE = table(
    labels={"informacion": {"es": "Información"}},
    values={
        "imageLabel": {
            "Retrato": {"ja": "肖像", "en": "Portrait"},
            "Armadura": {"ja": "鎧", "en": "Armour"},
        }
    },
    layouts={"persona": {"image": "imagen"}},
)
LAYOUT = TABLE.layouts["persona"]


def refs(images):
    return [i.ref for i in images]


def labels(images):
    return [i.label for i in images]


class Pairing(unittest.TestCase):
    def test_a_scalar_image_is_still_one_image(self):
        got = ib.resolve_images(TABLE, LAYOUT, {"imagen": "a.webp"}, "es", "es")
        self.assertEqual(refs(got), ["a.webp"])
        self.assertEqual(labels(got), [""])

    def test_images_and_labels_pair_by_position(self):
        fm = {"imagen": ["a.webp", "b.webp"], "imageLabel": ["Retrato", "Armadura"]}
        got = ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")
        self.assertEqual(refs(got), ["a.webp", "b.webp"])
        self.assertEqual(labels(got), ["Retrato", "Armadura"])

    def test_fewer_labels_than_images_leaves_the_rest_unlabelled(self):
        # The gallery still renders; the caller reports the mismatch.
        fm = {"imagen": ["a.webp", "b.webp"], "imageLabel": ["Retrato"]}
        self.assertEqual(labels(ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")),
                         ["Retrato", ""])

    def test_more_labels_than_images_drops_the_extras(self):
        fm = {"imagen": ["a.webp"], "imageLabel": ["Retrato", "Armadura"]}
        got = ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")
        self.assertEqual(refs(got), ["a.webp"])
        self.assertEqual(labels(got), ["Retrato"])

    def test_a_single_image_may_still_carry_a_label(self):
        # Worth having even with no tabs: it becomes the image's alt text.
        fm = {"imagen": "a.webp", "imageLabel": "Retrato"}
        self.assertEqual(labels(ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")),
                         ["Retrato"])

    def test_unusable_entries_are_dropped(self):
        fm = {"imagen": ["a.webp", "[[]]", "", "b.webp"]}
        self.assertEqual(refs(ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")),
                         ["a.webp", "b.webp"])

    def test_wrapped_and_stray_bracket_forms_are_unwrapped(self):
        fm = {"imagen": ["[[a.webp]]", "![[b.webp]]", "c.webp]]"]}
        self.assertEqual(refs(ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")),
                         ["a.webp", "b.webp", "c.webp"])


class Translation(unittest.TestCase):
    def test_labels_go_through_the_shared_vocabulary(self):
        fm = {"imagen": ["a.webp", "b.webp"], "imageLabel": ["Retrato", "Armadura"]}
        self.assertEqual(labels(ib.resolve_images(TABLE, LAYOUT, fm, "ja", "es")),
                         ["肖像", "鎧"])

    def test_a_per_note_override_wins_over_the_vocabulary(self):
        fm = {
            "imagen": ["a.webp", "b.webp"],
            "imageLabel": ["Retrato", "Armadura"],
            "imageLabel_ja": ["自画像", "甲冑"],
        }
        self.assertEqual(labels(ib.resolve_images(TABLE, LAYOUT, fm, "ja", "es")),
                         ["自画像", "甲冑"])

    def test_the_default_language_never_reads_a_suffixed_key(self):
        # `imageLabel_es` is dead weight while `es` is the default -- the same
        # rule every other `key_<lang>` follows, and easy to mistake for a bug.
        fm = {"imagen": ["a.webp"], "imageLabel": ["Retrato"], "imageLabel_es": ["NUNCA"]}
        self.assertEqual(labels(ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")),
                         ["Retrato"])

    def test_images_themselves_can_differ_per_language(self):
        fm = {"imagen": ["a.webp"], "imagen_ja": ["a-ja.webp"]}
        self.assertEqual(refs(ib.resolve_images(TABLE, LAYOUT, fm, "ja", "es")),
                         ["a-ja.webp"])
        self.assertEqual(refs(ib.resolve_images(TABLE, LAYOUT, fm, "es", "es")),
                         ["a.webp"])

    def test_an_untranslated_label_falls_back_to_what_was_written(self):
        fm = {"imagen": ["a.webp"], "imageLabel": ["Forma verdadera"]}
        self.assertEqual(labels(ib.resolve_images(TABLE, LAYOUT, fm, "ja", "es")),
                         ["Forma verdadera"])


class SizeAndFallback(unittest.TestCase):
    def test_a_size_written_into_the_link_wins(self):
        sized = dict(LAYOUT, imageSize="250")
        got = ib.resolve_images(TABLE, sized, {"imagen": ["a.webp|400"]}, "es", "es")
        self.assertEqual((got[0].ref, got[0].size), ("a.webp", "400"))

    def test_the_layout_size_applies_otherwise(self):
        sized = dict(LAYOUT, imageSize="250")
        got = ib.resolve_images(TABLE, sized, {"imagen": ["a.webp"]}, "es", "es")
        self.assertEqual((got[0].ref, got[0].size), ("a.webp", "250"))

    def test_an_empty_list_falls_back_to_the_layout_stand_in(self):
        layout = dict(LAYOUT, imageFallback="stand-in.svg")
        got = ib.resolve_images(TABLE, layout, {}, "es", "es")
        self.assertEqual(refs(got), ["stand-in.svg"])

    def test_the_stand_in_takes_no_label(self):
        # It is not a picture *of* anything, so a label would misdescribe it.
        layout = dict(LAYOUT, imageFallback="stand-in.svg")
        got = ib.resolve_images(TABLE, layout, {"imageLabel": ["Retrato"]}, "es", "es")
        self.assertEqual(labels(got), [""])

    def test_a_layout_with_no_image_key_has_no_images(self):
        self.assertEqual(ib.resolve_images(TABLE, {}, {"imagen": ["a.webp"]}, "es", "es"), [])


class DefaultImage(unittest.TestCase):
    """`defaultImageIndex` -- which image stands for the note.

    Zero-based, matching the `imagen[0]` an indexed Dataview column writes.
    It decides the image the infobox opens on *and* the one a roster row
    shows, because those are the same question asked twice.
    """

    THREE = ["a.webp", "b.webp", "c.webp"]

    def test_absent_means_the_first_image(self):
        # The ~99 single-image notes never write one, and must not change.
        self.assertEqual(ib.default_image_ref({"imagen": self.THREE}), "a.webp")
        self.assertEqual(ib.default_image_ref({"imagen": "only.webp"}), "only.webp")

    def test_it_picks_the_named_position(self):
        fm = {"imagen": self.THREE, "defaultImageIndex": 2}
        self.assertEqual(ib.default_image_ref(fm), "c.webp")

    def test_zero_is_explicit_and_valid(self):
        fm = {"imagen": self.THREE, "defaultImageIndex": 0}
        self.assertEqual(ib.default_image_ref(fm), "a.webp")
        self.assertEqual(ib.default_image_index(fm, 3), (0, True))

    def test_a_string_of_digits_is_accepted(self):
        # YAML gives an int, but a quoted value should not silently fail.
        fm = {"imagen": self.THREE, "defaultImageIndex": "2"}
        self.assertEqual(ib.default_image_ref(fm), "c.webp")

    def test_out_of_range_falls_back_and_is_flagged(self):
        for bad in (3, 99, -1):
            fm = {"imagen": self.THREE, "defaultImageIndex": bad}
            self.assertEqual(ib.default_image_ref(fm), "a.webp", bad)
            self.assertEqual(ib.default_image_index(fm, 3), (0, False), bad)

    def test_nonsense_falls_back_and_is_flagged(self):
        fm = {"imagen": self.THREE, "defaultImageIndex": "two"}
        self.assertEqual(ib.default_image_ref(fm), "a.webp")
        self.assertEqual(ib.default_image_index(fm, 3), (0, False))

    def test_the_index_counts_usable_images_only(self):
        # A placeholder entry is not a position a reader can choose.
        fm = {"imagen": ["[[]]", "b.webp", "c.webp"], "defaultImageIndex": 1}
        self.assertEqual(ib.default_image_ref(fm), "c.webp")

    def test_no_images_is_still_None(self):
        self.assertIsNone(ib.default_image_ref({"defaultImageIndex": 1}))

    def test_an_empty_value_is_treated_as_absent(self):
        fm = {"imagen": self.THREE, "defaultImageIndex": ""}
        self.assertEqual(ib.default_image_index(fm, 3), (0, True))


class SingleImageEntryPoint(unittest.TestCase):
    """`resolve_image` stays the one-picture answer, so old callers are safe."""

    def test_it_returns_the_first_of_several(self):
        fm = {"imagen": ["a.webp", "b.webp"]}
        self.assertEqual(ib.resolve_image(TABLE, LAYOUT, fm, "es", "es"), ("a.webp", None))

    def test_it_matches_resolve_images_for_a_scalar(self):
        fm = {"imagen": "a.webp"}
        self.assertEqual(ib.resolve_image(TABLE, LAYOUT, fm, "es", "es"), ("a.webp", None))

    def test_nothing_at_all_is_two_Nones(self):
        self.assertEqual(ib.resolve_image(TABLE, LAYOUT, {}, "es", "es"), (None, None))


class FirstImageRef(unittest.TestCase):
    """The helper the roster, the legacy infobox and licensing all share."""

    def test_it_accepts_either_shape(self):
        self.assertEqual(ib.first_image_ref("a.webp"), "a.webp")
        self.assertEqual(ib.first_image_ref(["a.webp", "b.webp"]), "a.webp")

    def test_it_skips_unusable_leading_entries(self):
        self.assertEqual(ib.first_image_ref(["[[]]", "", "b.webp"]), "b.webp")

    def test_nothing_usable_is_None(self):
        for value in (None, "", [], ["", "[[]]"], 42):
            self.assertIsNone(ib.first_image_ref(value), value)


class LabelKey(unittest.TestCase):
    def test_it_defaults_to_the_property_name(self):
        # Which is why no layout in the table needs editing for a gallery.
        self.assertEqual(ib.label_key_for({}), "imageLabel")

    def test_a_layout_may_point_somewhere_else(self):
        self.assertEqual(ib.label_key_for({"imageLabel": "etiquetas"}), "etiquetas")


if __name__ == "__main__":
    unittest.main()
