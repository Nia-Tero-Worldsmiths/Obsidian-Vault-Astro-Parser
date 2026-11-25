"""Maps with more than one base, and the control that switches between them.

The vault has one map with two bases -- Kenorland, physical and political --
and the plugin offers that switch as a pin pinned to the viewport. The site
does not reproduce the pin. It renders a labelled control instead, which is
why most of what is worth proving here is about *names*: that both bases
survive the read at all, that the control says which is which, and that it
says so in the reader's language.

The subtle one is `BothLanguagesInOneButton`. A module runs once per language
body, so the obvious thing is to render the active language and let `emit`
wrap the result -- and that would be wrong. `emit` decides what to share
between languages by comparing the rendered output, so a figure that differed
per language would be emitted once per language, which is the exact bug
`_merge_languages` documents: three Tambler maps, pan and zoom remembered per
language, hidden copies measuring their container as zero. So the figure has
to come out byte-identical in every pass, and the translation has to live
inside it rather than around it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.vault_parser import infobox as ib
from scripts.vault_parser import zoommap as zm
from scripts.vault_parser.model import Note
from scripts.vault_parser.modules.zoommap import BASE_VOCABULARY, ZoommapModule

TWO_BASES = {
    "imageBases": [
        {"path": "z_Assets/Maps/K.webp", "name": "Físico"},
        {"path": "z_Assets/Maps/K_pol.webp", "name": "Político"},
    ],
    "minZoom": 0.24,
    "maxZoom": 2,
}

TABLE = ib.InfoboxTable(
    available=True,
    values={BASE_VOCABULARY: {"Físico": {"ja": "地形図"}, "Político": {"ja": "行政図"}}},
)


def note(languages: list[str] | None = None) -> Note:
    return Note(
        vault_path="World Atlas/Kenorland.md",
        source=Path("/vault/World Atlas/Kenorland.md"),
        collection="atlas",
        slug="kenorland",
        title="Kenorland",
        breadcrumb=["World Atlas"],
        languages=languages or [],
    )


def module(table: ib.InfoboxTable = TABLE) -> ZoommapModule:
    mod = ZoommapModule(enabled=True)
    mod._icons = {}
    mod._table = table
    mod._default_lang = "es"
    return mod


def resolved(spec: zm.MapSpec) -> list[tuple[zm.Base, str]]:
    return [(base, f"/vault-assets/{Path(base.path).name}") for base in spec.bases]


class Bases(unittest.TestCase):
    """Reading `imageBases`, all of it."""

    def test_every_base_is_kept_in_the_order_the_fence_declares(self):
        spec = zm.build(TWO_BASES, None)
        self.assertEqual(
            [(b.path, b.name) for b in spec.bases],
            [("z_Assets/Maps/K.webp", "Físico"), ("z_Assets/Maps/K_pol.webp", "Político")],
        )

    def test_the_first_declared_base_is_the_one_the_map_opens_on(self):
        # Not the markers file's `activeBase`, which records where the author
        # last left the editor. The note's own order is what it means to show.
        self.assertEqual(zm.build(TWO_BASES, None).bases[0].name, "Físico")

    def test_a_bare_string_entry_is_a_path_with_no_name(self):
        spec = zm.build({"imageBases": ["maps/plain.webp"]}, None)
        self.assertEqual([(b.path, b.name) for b in spec.bases], [("maps/plain.webp", "")])

    def test_an_entry_with_no_path_is_dropped_rather_than_rendered_broken(self):
        spec = zm.build({"imageBases": [{"name": "Huérfano"}, {"path": "a.webp"}]}, None)
        self.assertEqual([b.path for b in spec.bases], ["a.webp"])

    def test_a_fence_whose_entries_all_lack_a_path_is_an_error(self):
        with self.assertRaises(zm.ZoommapError):
            zm.build({"imageBases": [{"name": "Solo un nombre"}]}, None)

    def test_a_fence_with_no_imagebases_is_an_error(self):
        with self.assertRaises(zm.ZoommapError):
            zm.build({"markers": "m.json"}, None)


class SwitchMarkers(unittest.TestCase):
    """The plugin's base-switch pin is superseded here, not unsupported."""

    def _markers(self, markers: list[dict]) -> tuple[list, list[str]]:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "m.markers.json"
            path.write_text(
                json.dumps({"size": {"w": 100, "h": 50}, "markers": markers}),
                encoding="utf-8",
            )
            config = type("C", (), {"vault_root": Path(root)})()
            found, _, _, warnings = zm.load_markers("m.markers.json", config)
            return found, warnings

    def test_a_switch_marker_becomes_no_pin_and_no_complaint(self):
        found, warnings = self._markers([{"type": "switch", "x": 0.1, "y": 0.1}])
        self.assertEqual(found, [])
        self.assertEqual(warnings, [])

    def test_a_genuinely_unsupported_type_is_still_reported(self):
        found, warnings = self._markers([{"type": "ruler", "x": 0.1, "y": 0.1}])
        self.assertEqual(found, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("ruler", warnings[0])

    def test_ordinary_pins_are_unaffected(self):
        found, warnings = self._markers(
            [{"type": "switch", "x": 0, "y": 0}, {"type": "pin", "x": 0.5, "y": 0.5}]
        )
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].x, found[0].y), (0.5, 0.5))
        self.assertEqual(warnings, [])


class Switcher(unittest.TestCase):
    """The control itself."""

    def test_two_bases_get_a_button_each(self):
        got = module()._switcher(resolved(zm.build(TWO_BASES, None)), note())
        self.assertEqual(got.count("zoommap-base-btn"), 2)
        self.assertIn('data-base="0"', got)
        self.assertIn('data-base="1"', got)

    def test_the_first_base_is_the_pressed_one(self):
        got = module()._switcher(resolved(zm.build(TWO_BASES, None)), note())
        first = got.index('data-base="0"')
        self.assertIn('aria-pressed="true"', got[:first])
        self.assertIn('aria-pressed="false"', got[first:])

    def test_a_single_base_map_gets_no_control_at_all(self):
        spec = zm.build({"imageBases": [{"path": "a.webp", "name": "Solo"}]}, None)
        self.assertEqual(module()._switcher(resolved(spec), note()), "")

    def test_a_nameless_base_is_labelled_by_its_position(self):
        spec = zm.build({"imageBases": ["a.webp", "b.webp"]}, None)
        got = module()._switcher(resolved(spec), note())
        self.assertIn(">1</button>", got)
        self.assertIn(">2</button>", got)


class BothLanguagesInOneButton(unittest.TestCase):
    """Every language ships in the markup; CSS hides the rest."""

    def test_a_bilingual_note_gets_one_section_per_language(self):
        got = module()._label("Físico", note(["es", "ja"]))
        self.assertEqual(
            got,
            '<span class="lang-section" data-lang="es">Físico</span>'
            '<span class="lang-section" data-lang="ja">地形図</span>',
        )

    def test_a_monolingual_note_gets_the_bare_name(self):
        self.assertEqual(module()._label("Físico", note(["es"])), "Físico")

    def test_the_markup_is_identical_whatever_pass_is_running(self):
        # The guarantee `emit` depends on to keep one map rather than three.
        mod = module()
        page = note(["es", "ja"])
        page.active_language = "es"
        first = mod._switcher(resolved(zm.build(TWO_BASES, None)), page)
        page.active_language = "ja"
        second = mod._switcher(resolved(zm.build(TWO_BASES, None)), page)
        self.assertEqual(first, second)

    def test_the_vault_language_is_never_looked_up(self):
        mod = module(ib.InfoboxTable(available=True, values={}))
        self.assertEqual(mod._translate("Físico", "es"), "Físico")
        self.assertEqual(mod.stats.warnings, [])

    def test_an_untranslated_name_falls_back_and_says_so(self):
        mod = module(ib.InfoboxTable(available=True, values={}))
        self.assertEqual(mod._translate("Político", "ja"), "Político")
        self.assertEqual(len(mod.stats.warnings), 1)
        self.assertIn("Político", mod.stats.warnings[0])
        self.assertIn(BASE_VOCABULARY, mod.stats.warnings[0])


class Markup(unittest.TestCase):
    """The figure the module emits."""

    def _figure(self, fence: dict, page: Note | None = None) -> str:
        spec = zm.build(fence, None)
        return module()._markup(spec, resolved(spec), page or note(["es", "ja"]), None)

    def test_every_base_ships_and_only_the_first_one_shows(self):
        got = self._figure(TWO_BASES)
        self.assertIn('src="/vault-assets/K.webp" alt="Kenorland" data-base="0"', got)
        self.assertIn('data-base="1"', got)
        # Exactly one hidden image: the one that is not the opening base.
        self.assertEqual(got.count("hidden /"), 1)

    def test_a_single_base_map_emits_what_it_always_did(self):
        got = self._figure({"imageBases": [{"path": "a.webp"}]})
        self.assertNotIn("zoommap-bases", got)
        self.assertNotIn("hidden", got)
        self.assertEqual(got.count("zoommap-base"), 1)

    def test_a_pixel_box_becomes_a_ratio_so_it_can_be_shrunk(self):
        # `max-width: 100%` caps the width; a fixed height would ignore the
        # cap and letterbox the map. 678x466 is Kenorland's own box.
        got = self._figure(dict(TWO_BASES, width="678px", height="466px"))
        self.assertIn("width:678px;aspect-ratio:678 / 466", got)
        self.assertNotIn("height:466px", got)

    def test_a_height_in_another_unit_is_left_exactly_as_written(self):
        got = self._figure(dict(TWO_BASES, width="678px", height="60vh"))
        self.assertIn("width:678px;height:60vh", got)
        self.assertNotIn("aspect-ratio", got)

    def test_a_box_with_no_height_still_reserves_the_image_proportions(self):
        # Unchanged behaviour: the ratio comes from the markers file's size.
        spec = zm.MapSpec(bases=[zm.Base(path="a.webp")], width=2048, height=1536)
        got = module()._markup(spec, resolved(spec), note(["es"]), None)
        self.assertIn("width:100%;aspect-ratio:2048 / 1536", got)

    def test_the_switcher_sits_outside_the_panning_surface(self):
        # Inside it, pressing a button would read as the start of a drag.
        got = self._figure(TWO_BASES)
        self.assertLess(got.index("</div></div>"), got.index("zoommap-bases"))


if __name__ == "__main__":
    unittest.main()
