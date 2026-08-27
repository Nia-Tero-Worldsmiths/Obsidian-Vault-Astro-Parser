"""Ported from the i18n Manager plugin's `tests/infoboxModel.test.ts`.

The parser is a *second* implementation of rules that exist in exactly one
other place. These cases are the plugin authors' own statement of what those
rules are, so they are kept as close to the originals as Python allows --
same names, same fixtures, same order. When the plugin's suite gains a case,
port it here too.

Run with `python -m unittest discover -s tests`.
"""

from __future__ import annotations

import unittest

import yaml

from scripts.vault_parser import infobox as ib


NEWLINE = chr(10)
TL_LABEL_YAML = (
    "TL:" + NEWLINE
    + '  es: "[[Nivel Tecnologico]]"' + NEWLINE
    + '  prefix: "TL"' + NEWLINE
)


def table(**kwargs) -> ib.InfoboxTable:
    kwargs.setdefault("available", True)
    return ib.InfoboxTable(**kwargs)


TABLE = table(
    labels={
        "ubicacion": {"es": "Ubicado en", "ja": "所在地", "en": "Located in"},
        "gobernador": {"es": "Gobernado por", "ja": "統治者"},
        "edad": {"es": "Edad", "ja": "年齢", "suffix": {"es": "años", "ja": "歳"}},
        "especie": {"es": "Especie", "ja": "種族"},
        "informacion": {"es": "Información", "ja": "情報"},
    },
    values={"especie": {"Humano": {"ja": "人間", "en": "Human"}}},
    layouts={
        "lugar": {"heading": "informacion", "rows": ["ubicacion", "gobernador"]},
        "persona": {
            "sections": [
                {"heading": "informacion", "rows": ["especie", "edad"]},
                {"rows": ["ubicacion"]},
            ]
        },
    },
)


class NoteTypeNormalization(unittest.TestCase):
    def test_folds_case_and_accents(self):
        # Every template uses a lowercase slug except Misión.
        self.assertEqual(ib.normalize_type_key("Misión"), "mision")
        self.assertEqual(ib.normalize_type_key("  LUGAR "), "lugar")
        self.assertEqual(ib.normalize_type_key(None), "")
        self.assertEqual(ib.normalize_type_key(42), "")

    def test_layout_lookup_matches_regardless_of_casing(self):
        self.assertIsNotNone(TABLE.layout_for("lugar"))
        self.assertIsNotNone(TABLE.layout_for("LUGAR"))
        self.assertIsNone(TABLE.layout_for("nonesuch"))
        self.assertIsNone(TABLE.layout_for(""))

    def test_layout_sections_normalizes_both_shapes(self):
        single = ib._layout_sections(TABLE.layouts["lugar"])
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0]["heading"], "informacion")
        self.assertEqual(len(ib._layout_sections(TABLE.layouts["persona"])), 2)
        self.assertEqual(ib._layout_sections({}), [])


class Labels(unittest.TestCase):
    def test_falls_back_to_default_then_raw_key(self):
        self.assertEqual(ib.resolve_label(TABLE, "ubicacion", "ja", "es"), "所在地")
        # No `en` entry for gobernador -> the default language's label.
        self.assertEqual(ib.resolve_label(TABLE, "gobernador", "en", "es"), "Gobernado por")
        # Not in the table at all -> the key, which says what to add.
        self.assertEqual(ib.resolve_label(TABLE, "gentilicio", "es", "es"), "gentilicio")

    def test_suffixes_are_per_language_and_optional(self):
        self.assertEqual(ib.resolve_suffix(TABLE, "edad", "es", "es"), "años")
        self.assertEqual(ib.resolve_suffix(TABLE, "edad", "ja", "es"), "歳")
        self.assertEqual(ib.resolve_suffix(TABLE, "edad", "en", "es"), "años")
        self.assertEqual(ib.resolve_suffix(TABLE, "especie", "es", "es"), "")

    def test_suffix_is_not_mistaken_for_a_label(self):
        # `suffix` sits alongside the language codes; only strings are labels.
        self.assertEqual(ib.resolve_label(TABLE, "edad", "suffix", "es"), "Edad")


class Values(unittest.TestCase):
    def test_per_note_translation_wins_over_base(self):
        fm = {"especie": "Humano", "especie_ja": "人造人間"}
        self.assertEqual(ib.resolve_value(TABLE, fm, "especie", "ja", "es"), ["人造人間"])

    def test_missing_translation_falls_back_to_base(self):
        fm = {"gentilicio": "Marino"}
        self.assertEqual(ib.resolve_value(TABLE, fm, "gentilicio", "ja", "es"), ["Marino"])

    def test_controlled_vocabulary_translates_shared_values(self):
        fm = {"especie": "Humano"}
        self.assertEqual(ib.resolve_value(TABLE, fm, "especie", "ja", "es"), ["人間"])
        self.assertEqual(ib.resolve_value(TABLE, fm, "especie", "en", "es"), ["Human"])

    def test_hand_written_translation_is_never_overridden(self):
        fm = {"especie": "Humano", "especie_ja": "人造人間"}
        self.assertEqual(ib.resolve_value(TABLE, fm, "especie", "ja", "es"), ["人造人間"])

    def test_default_language_never_consults_the_vocabulary(self):
        fm = {"especie": "Humano"}
        self.assertEqual(ib.resolve_value(TABLE, fm, "especie", "es", "es"), ["Humano"])

    def test_all_behaves_as_the_default_language(self):
        fm = {"especie": "Humano", "especie_ja": "人造人間"}
        self.assertEqual(ib.resolve_value(TABLE, fm, "especie", ib.ALL, "es"), ["Humano"])

    def test_lists_resolve_element_wise(self):
        fm = {"especie": ["Humano", "Yaksha"]}
        self.assertEqual(ib.resolve_value(TABLE, fm, "especie", "ja", "es"), ["人間", "Yaksha"])

    def test_absent_and_empty_values_yield_none(self):
        self.assertIsNone(ib.resolve_value(TABLE, {}, "especie", "es", "es"))
        self.assertIsNone(ib.resolve_value(TABLE, {"especie": None}, "especie", "es", "es"))
        self.assertIsNone(ib.resolve_value(TABLE, {"especie": ""}, "especie", "es", "es"))
        self.assertIsNone(ib.resolve_value(TABLE, {"especie": []}, "especie", "es", "es"))
        self.assertIsNone(ib.resolve_value(TABLE, {"especie": ["", "  "]}, "especie", "es", "es"))

    def test_zero_and_false_are_values_not_emptiness(self):
        self.assertEqual(ib.resolve_value(TABLE, {"edad": 0}, "edad", "es", "es"), ["0"])
        self.assertEqual(ib.resolve_value(TABLE, {"edad": False}, "edad", "es", "es"), ["False"])


class RowShape(unittest.TestCase):
    def test_every_layout_row_is_kept_with_a_placeholder(self):
        # An infobox is a fixed shape per NoteType; a half-filled note shows
        # the same table as a complete one.
        [section] = ib.resolve_infobox(TABLE, TABLE.layouts["lugar"], {"ubicacion": "X"}, "es", "es")
        self.assertEqual([r.key for r in section.rows], ["ubicacion", "gobernador"])
        self.assertEqual(section.rows[1].values, ["-"])
        self.assertTrue(section.rows[1].missing)
        self.assertFalse(section.rows[0].missing)

    def test_a_missing_value_carries_no_unit_suffix(self):
        # "- años" would read as an actual quantity.
        [section, _] = ib.resolve_infobox(TABLE, TABLE.layouts["persona"], {}, "es", "es")
        edad = next(r for r in section.rows if r.key == "edad")
        self.assertTrue(edad.missing)
        self.assertEqual(edad.suffix, "")

    def test_the_placeholder_is_configurable(self):
        custom = table(labels=TABLE.labels, layouts=TABLE.layouts, placeholder="—")
        [section] = ib.resolve_infobox(custom, custom.layouts["lugar"], {}, "es", "es")
        self.assertEqual(section.rows[0].values, ["—"])

    def test_an_entirely_empty_note_still_renders_the_full_table(self):
        [section] = ib.resolve_infobox(TABLE, TABLE.layouts["lugar"], {}, "es", "es")
        self.assertEqual(len(section.rows), 2)
        self.assertTrue(all(r.missing for r in section.rows))

    def test_multi_section_and_unheaded_sections(self):
        sections = ib.resolve_infobox(TABLE, TABLE.layouts["persona"], {"especie": "Humano"}, "es", "es")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].heading, "Información")
        self.assertEqual(sections[1].heading, "")

    def test_an_empty_table_degrades_instead_of_raising(self):
        empty = table()
        self.assertEqual(ib.resolve_infobox(empty, {}, {}, "es", "es"), [])
        self.assertIsNone(empty.layout_for("lugar"))


class Title(unittest.TestCase):
    def test_prefers_title_lang_then_title_then_filename(self):
        fm = {"title": "Gran Pantano", "title_ja": "大湿地"}
        self.assertEqual(ib.resolve_title({}, fm, "ja", "es", "file"), "大湿地")
        self.assertEqual(ib.resolve_title({}, fm, "es", "es", "file"), "Gran Pantano")
        self.assertEqual(ib.resolve_title({}, fm, "en", "es", "file"), "Gran Pantano")
        self.assertEqual(ib.resolve_title({}, {}, "es", "es", "file"), "file")
        # A bare `title:` in a template is whitespace-only, not a title.
        self.assertEqual(ib.resolve_title({}, {"title": "   "}, "es", "es", "file"), "file")
        self.assertEqual(ib.resolve_title({}, {"title": 42}, "es", "es", "file"), "file")


class YamlBody(unittest.TestCase):
    def test_a_plain_yaml_file_is_used_as_is(self):
        self.assertEqual(ib.extract_yaml_body("labels:\n  a: b\n"), "labels:\n  a: b\n")

    def test_a_fenced_yaml_block_inside_a_note_is_extracted(self):
        raw = "Some prose.\n\n```yaml\nlabels:\n  a: b\n```\n\nMore prose.\n"
        self.assertEqual(ib.extract_yaml_body(raw), "labels:\n  a: b\n")

    def test_yml_spelling_and_extra_backticks(self):
        raw = "````YML\nlabels: {}\n````\n"
        self.assertEqual(ib.extract_yaml_body(raw).strip(), "labels: {}")

    def test_text_with_no_fence_falls_through(self):
        self.assertEqual(ib.extract_yaml_body("no fence here"), "no fence here")


class YamlSanitizer(unittest.TestCase):
    """PyYAML rejects the optional-row marker inside a flow sequence.

    Obsidian's parser accepts `[clase, subclase?]`; PyYAML treats `?` as an
    indicator and refuses the whole document, which would take every infobox
    in the vault down with it. The real vault table hits this.
    """

    def test_an_optional_marker_in_a_flow_sequence_is_quoted(self):
        raw = "rows: [clase, subclase?]"
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load(raw)
        self.assertEqual(
            yaml.safe_load(ib.sanitize_yaml(raw)), {"rows": ["clase", "subclase?"]}
        )

    def test_a_wikilink_label_is_left_alone(self):
        # `[[Nivel Tecnologico]]` must not be mistaken for a flow sequence.
        raw = TL_LABEL_YAML
        self.assertEqual(ib.sanitize_yaml(raw), raw)
        self.assertEqual(
            yaml.safe_load(raw)["TL"]["es"], "[[Nivel Tecnologico]]"
        )

    def test_ordinary_flow_sequences_are_untouched(self):
        raw = "rows: [ubicacion, gobernador, perteneceA]"
        self.assertEqual(ib.sanitize_yaml(raw), raw)

    def test_a_block_sequence_needs_no_help(self):
        raw = "rows:" + NEWLINE + "  - clase" + NEWLINE + "  - subclase?" + NEWLINE
        self.assertEqual(ib.sanitize_yaml(raw), raw)

    def test_holes_in_a_flow_sequence_survive(self):
        # `subclase: [, Tierra]` means the subclass belongs to the second class.
        self.assertEqual(
            yaml.safe_load(ib.sanitize_yaml("s: [, Tierra]")), {"s": [None, "Tierra"]}
        )


class Images(unittest.TestCase):
    def test_unwraps_every_shape_the_vault_contains(self):
        self.assertEqual(ib.normalize_image_ref("castle-flag.svg"), "castle-flag.svg")
        self.assertEqual(ib.normalize_image_ref("[[Sonia.png]]"), "Sonia.png")
        self.assertEqual(ib.normalize_image_ref("![[Sonia.png]]"), "Sonia.png")
        self.assertEqual(ib.normalize_image_ref("Sonia.png]]"), "Sonia.png")
        self.assertEqual(ib.normalize_image_ref("  Sonia.png  "), "Sonia.png")

    def test_empty_template_placeholders_yield_no_image(self):
        for empty in ("[[]]", "![[]]", "", "   ", None, 42):
            self.assertIsNone(ib.normalize_image_ref(empty), empty)

    def test_resolve_image_builds_one_reference_or_nothing(self):
        layout = {"image": "imagen"}
        self.assertEqual(ib.resolve_image(TABLE, layout, {"imagen": "a.png"}, "es", "es"), ("a.png", None))
        self.assertEqual(ib.resolve_image(TABLE, layout, {"imagen": "![[]]"}, "es", "es"), (None, None))
        self.assertEqual(ib.resolve_image(TABLE, {}, {"imagen": "a.png"}, "es", "es"), (None, None))

    def test_a_per_language_image_is_honoured(self):
        layout = {"image": "imagen"}
        fm = {"imagen": "mapa.png", "imagen_ja": "mapa-ja.png"}
        self.assertEqual(ib.resolve_image(TABLE, layout, fm, "ja", "es")[0], "mapa-ja.png")
        self.assertEqual(ib.resolve_image(TABLE, layout, fm, "es", "es")[0], "mapa.png")

    def test_image_size_never_overrides_an_authors_own(self):
        sized = {"image": "imagen", "imageSize": "250"}
        self.assertEqual(ib.resolve_image(TABLE, sized, {"imagen": "a.png"}, "es", "es"), ("a.png", "250"))
        # The note already said 400 inside the link; that wins.
        self.assertEqual(
            ib.resolve_image(TABLE, sized, {"imagen": "[[a.png|400]]"}, "es", "es"), ("a.png", "400")
        )


STATUS_TABLE = table(
    status={
        "Vivo": {"symbol": "", "es": "Vivo", "ja": "生存"},
        "Muerto": {"symbol": "†", "es": "Muerto", "ja": "死亡"},
        "_sin_reconocer": {"symbol": "⚠", "es": "no reconocido", "fallback": True},
    },
)
STATUS_LAYOUT = {"status": "estado"}


class Status(unittest.TestCase):
    def test_a_known_status_resolves_to_symbol_and_tooltip(self):
        got = ib.resolve_status(STATUS_TABLE, STATUS_LAYOUT, {"estado": "Muerto"}, "ja", "es")
        self.assertEqual((got.symbol, got.tooltip, got.fallback), ("†", "死亡", False))

    def test_alive_resolves_to_an_empty_symbol(self):
        got = ib.resolve_status(STATUS_TABLE, STATUS_LAYOUT, {"estado": "Vivo"}, "es", "es")
        self.assertEqual(got.symbol, "")
        self.assertFalse(got.fallback)

    def test_matching_ignores_case_and_accents(self):
        got = ib.resolve_status(STATUS_TABLE, STATUS_LAYOUT, {"estado": "  muerto "}, "es", "es")
        self.assertEqual(got.symbol, "†")
        self.assertFalse(got.fallback)

    def test_an_unrecognised_value_falls_through_to_the_fallback(self):
        got = ib.resolve_status(STATUS_TABLE, STATUS_LAYOUT, {"estado": "typo"}, "es", "es")
        self.assertEqual(got.symbol, "⚠")
        self.assertTrue(got.fallback)

    def test_no_key_empty_key_or_non_scalar_shows_nothing(self):
        for value in (None, "", "   ", ["Vivo"], {"a": 1}):
            self.assertIsNone(
                ib.resolve_status(STATUS_TABLE, STATUS_LAYOUT, {"estado": value}, "es", "es"), value
            )
        self.assertIsNone(ib.resolve_status(STATUS_TABLE, STATUS_LAYOUT, {}, "es", "es"))

    def test_no_status_key_or_no_status_table_shows_nothing(self):
        self.assertIsNone(ib.resolve_status(STATUS_TABLE, {}, {"estado": "Muerto"}, "es", "es"))
        self.assertIsNone(ib.resolve_status(table(), STATUS_LAYOUT, {"estado": "Muerto"}, "es", "es"))

    def test_with_no_fallback_entry_an_unrecognised_value_shows_nothing(self):
        bare = table(status={"Vivo": {"symbol": "", "es": "Vivo"}})
        self.assertIsNone(ib.resolve_status(bare, STATUS_LAYOUT, {"estado": "typo"}, "es", "es"))


class Decorations(unittest.TestCase):
    DEC = table(
        labels={
            "TL": {"es": "Nivel", "prefix": "TL"},
            "edad": {"es": "Edad", "suffix": {"es": "años", "ja": "歳"}},
            "plain": {"es": "Plain"},
        },
    )

    def test_a_plain_string_decoration_is_used_for_every_language(self):
        self.assertEqual(ib.resolve_prefix(self.DEC, "TL", "es", "es"), "TL")
        self.assertEqual(ib.resolve_prefix(self.DEC, "TL", "ja", "es"), "TL")

    def test_a_mapped_decoration_follows_the_language(self):
        self.assertEqual(ib.resolve_suffix(self.DEC, "edad", "ja", "es"), "歳")
        # No `en` entry -> the default language's form.
        self.assertEqual(ib.resolve_suffix(self.DEC, "edad", "en", "es"), "años")

    def test_an_absent_decoration_is_the_empty_string(self):
        self.assertEqual(ib.resolve_prefix(self.DEC, "plain", "es", "es"), "")
        self.assertEqual(ib.resolve_suffix(self.DEC, "missing", "es", "es"), "")

    def test_prefix_and_suffix_reach_the_resolved_row(self):
        layout = {"rows": ["TL", "edad"]}
        [section] = ib.resolve_infobox(self.DEC, layout, {"TL": "3", "edad": "34"}, "es", "es")
        self.assertEqual((section.rows[0].prefix, section.rows[0].values), ("TL", ["3"]))
        self.assertEqual((section.rows[1].values, section.rows[1].suffix), (["34"], "años"))

    def test_a_missing_value_carries_neither_prefix_nor_suffix(self):
        layout = {"rows": ["TL", "edad"]}
        [section] = ib.resolve_infobox(self.DEC, layout, {}, "es", "es")
        self.assertTrue(all(r.prefix == "" and r.suffix == "" for r in section.rows))


class OptionalRows(unittest.TestCase):
    OPT = table(
        labels={"a": {"es": "A"}, "b": {"es": "B"}},
        layouts={"t": {"rows": ["a", "b?"]}},
    )

    def test_the_optional_marker_is_stripped_off_the_key(self):
        self.assertEqual(ib.parse_row_spec("creencia?"), ib.RowSpec("creencia", True))
        self.assertEqual(ib.parse_row_spec("creencia"), ib.RowSpec("creencia", False))
        self.assertEqual(ib.parse_row_spec("  creencia?  "), ib.RowSpec("creencia", True))

    def test_empty_optional_disappears_empty_mandatory_keeps_its_dash(self):
        [section] = ib.resolve_infobox(self.OPT, self.OPT.layouts["t"], {}, "es", "es")
        self.assertEqual([r.key for r in section.rows], ["a"])
        self.assertTrue(section.rows[0].missing)

    def test_a_filled_optional_row_is_shown_like_any_other(self):
        [section] = ib.resolve_infobox(self.OPT, self.OPT.layouts["t"], {"a": "x", "b": "y"}, "es", "es")
        self.assertEqual([r.key for r in section.rows], ["a", "b"])
        self.assertFalse(section.rows[1].missing)

    def test_a_section_all_optional_and_all_empty_renders_nothing(self):
        layout = {"rows": ["a?", "b?"]}
        [section] = ib.resolve_infobox(self.OPT, layout, {}, "es", "es")
        self.assertEqual(section.rows, [])

    def test_optionality_does_not_leak_into_the_resolved_key(self):
        [section] = ib.resolve_infobox(self.OPT, self.OPT.layouts["t"], {"b": "y"}, "es", "es")
        self.assertIn("b", [r.key for r in section.rows])
        self.assertNotIn("b?", [r.key for r in section.rows])


GROUP_TABLE = table(
    labels={"clase": {"es": "Clase"}, "subclase": {"es": "└> Subclase"}, "elan": {"es": "Elan"}},
    values={"clase": {"Maga": {"ja": "魔法使い"}}},
)
GROUP_LAYOUT = {"sections": [{"rows": [{"group": ["clase", "subclase?"]}, "elan?"]}]}


def _orphan(child: str, parent: str) -> str:
    return f"{child}!{parent}"


class PairedRows(unittest.TestCase):
    def resolve(self, fm, lang="es", layout=GROUP_LAYOUT):
        return ib.resolve_infobox(GROUP_TABLE, layout, fm, lang, "es", _orphan)[0]

    def test_a_single_class_is_unnumbered(self):
        section = self.resolve({"clase": "Clériga", "subclase": "Tierra"})
        self.assertEqual([r.label for r in section.rows], ["Clase", "└> Subclase"])
        self.assertEqual([r.values[0] for r in section.rows], ["Clériga", "Tierra"])

    def test_two_classes_pair_with_their_own_subclasses(self):
        section = self.resolve(
            {"clase": ["Rogue", "Maga"], "subclase": ["Arcane Trickster", "Bladesinger"]}
        )
        self.assertEqual(
            [f"{r.label}: {r.values[0]}" for r in section.rows],
            [
                "Clase 1: Rogue",
                "└> Subclase: Arcane Trickster",
                "Clase 2: Maga",
                "└> Subclase: Bladesinger",
            ],
        )

    def test_a_class_with_no_subclass_has_no_subclass_row(self):
        # Two classes, one subclass; the gap says which class it belongs to.
        section = self.resolve({"clase": ["Kineticista", "Guardiana"], "subclase": [None, "Tierra"]})
        self.assertEqual(
            [f"{r.label}: {r.values[0]}" for r in section.rows],
            ["Clase 1: Kineticista", "Clase 2: Guardiana", "└> Subclase: Tierra"],
        )
        self.assertTrue(all(not r.problem for r in section.rows))

    def test_a_subclass_with_no_class_is_kept_and_flagged(self):
        section = self.resolve({"clase": [None, "Guardiana"], "subclase": ["Tierra", "Bestia"]})
        orphaned = next((r for r in section.rows if r.problem), None)
        self.assertIsNotNone(orphaned, "the orphan row must survive so the author sees it")
        self.assertEqual(orphaned.key, "subclase")
        self.assertEqual(orphaned.values[0], "Tierra")
        self.assertEqual(orphaned.problem, "subclase!clase")

    def test_vocabulary_still_applies_inside_a_group(self):
        section = self.resolve({"clase": ["Maga"], "subclase": []}, lang="ja")
        self.assertEqual(section.rows[0].values[0], "魔法使い")

    def test_an_entirely_empty_group_still_shows_its_mandatory_placeholder(self):
        section = self.resolve({})
        self.assertEqual([r.key for r in section.rows], ["clase"])
        self.assertTrue(section.rows[0].missing)

    def test_a_group_of_one_key_behaves_like_a_repeated_row(self):
        layout = {"sections": [{"rows": [{"group": ["clase"]}]}]}
        section = self.resolve({"clase": ["A", "B"]}, layout=layout)
        self.assertEqual([r.label for r in section.rows], ["Clase 1", "Clase 2"])

    def test_only_the_parent_of_a_group_is_numbered(self):
        section = self.resolve(
            {"clase": ["Rogue", "Maga"], "subclase": ["Arcane Trickster", "Bladesinger"]}
        )
        self.assertEqual(
            [r.label for r in section.rows],
            ["Clase 1", "└> Subclase", "Clase 2", "└> Subclase"],
        )


if __name__ == "__main__":
    unittest.main()
