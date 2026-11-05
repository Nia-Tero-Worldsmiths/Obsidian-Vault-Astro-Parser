"""The ```i18n-query fence: choosing an organisation's members, and the markup.

The cases that matter here are the ones the ```dataview rosters this replaces
could not express -- membership through `organizacion` rather than a tag, and
one written term matching the vault's three different spellings of the same
organisation.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.vault_parser import grouping, infobox as ib
from scripts.vault_parser.model import Note, index_key
from scripts.vault_parser.modules import i18n_query


def note(name: str, **kwargs) -> Note:
    frontmatter = kwargs.pop("frontmatter", {})
    tags = kwargs.pop("tags", [])
    return Note(
        vault_path=kwargs.pop("vault_path", f"World/{name}.md"),
        source=Path(f"/vault/World/{name}.md"),
        collection="encyclopedia",
        slug=name.lower().replace(" ", "-"),
        title=kwargs.pop("title", name),
        breadcrumb=["World"],
        frontmatter=frontmatter,
        tags=tags,
        **kwargs,
    )


class Ctx:
    """Just enough context for the membership predicates.

    `find_note` is the only thing they touch, and stubbing it keeps these tests
    free of the vault walk and the whole config object. It resolves with the
    real `index_key`, which only casefolds -- stubbing it any more leniently
    would hide the very mistake that made `field_links_to` take its target as
    an argument instead of looking it up from the folded term.
    """

    def __init__(self, notes: dict[str, Note] | None = None) -> None:
        self._notes = {index_key(k): v for k, v in (notes or {}).items()}

    def find_note(self, reference: str) -> Note | None:
        return self._notes.get(index_key(reference))


class TermFolding(unittest.TestCase):
    """One written term against the vault's inconsistent spellings."""

    def test_the_three_real_spellings_fold_together(self):
        # Each row is a tag and an `organizacion` value naming one group.
        for tag, written in (
            ("blackShawarma", "Black Shawarma"),
            ("corazasPetreas", "Corazas Pétreas"),
            ("custodios-de-juutei", "Custodios de Juutei"),
        ):
            self.assertEqual(
                grouping.normalize_term(tag),
                grouping.normalize_term(written),
                tag,
            )

    def test_it_folds_case_accents_and_separators(self):
        self.assertEqual(grouping.normalize_term("Corazas Pétreas"), "corazaspetreas")
        self.assertEqual(grouping.normalize_term("custodios_de_juutei"), "custodiosdejuutei")
        self.assertEqual(grouping.normalize_term("  Black   Shawarma  "), "blackshawarma")

    def test_an_en_dash_folds_like_a_hyphen(self):
        # Obsidian's smart punctuation turns a typed "-" into these.
        self.assertEqual(grouping.normalize_term("ex–inquisidor"), "exinquisidor")
        self.assertEqual(grouping.normalize_term("ex—inquisidor"), "exinquisidor")

    def test_nothing_usable_is_empty(self):
        for value in (None, "", "   ", 42, ["a"]):
            self.assertEqual(grouping.normalize_term(value), "", repr(value))

    def test_it_does_not_widen_the_NoteType_key(self):
        # `normalize_type_key` matches a NoteType against a layout name. If it
        # dropped separators too, two distinct layouts could start colliding.
        self.assertEqual(ib.normalize_type_key("Mi Tipo"), "mi tipo")
        self.assertEqual(grouping.normalize_term("Mi Tipo"), "mitipo")


class MembershipByTag(unittest.TestCase):
    def test_a_tag_matches_however_it_is_spelled(self):
        member = note("Moby", tags=["PJ", "custodios-de-juutei"])
        self.assertTrue(grouping.has_tag(member, "custodiosdejuutei"))

    def test_an_unrelated_tag_does_not_match(self):
        self.assertFalse(grouping.has_tag(note("Moby", tags=["PJ"]), "custodiosdejuutei"))

    def test_an_empty_term_matches_nobody(self):
        # Otherwise a fence with no term and an unnamed note would sweep in
        # every note that happens to carry an empty tag.
        self.assertFalse(grouping.has_tag(note("Moby", tags=["PJ"]), ""))


class MembershipByOrganizacion(unittest.TestCase):
    """The half that finds Briar, who carries no tags at all."""

    def test_a_wikilink_value_matches_the_term(self):
        briar = note("Briar", frontmatter={"organizacion": "[[Custodios de Juutei]]"})
        self.assertTrue(
            grouping.field_links_to(briar, "organizacion", "custodiosdejuutei", Ctx())
        )

    def test_a_list_of_organisations_matches_any_of_them(self):
        member = note(
            "Diana",
            frontmatter={"organizacion": ["[[Black Shawarma]]", "[[Custodios de Juutei]]"]},
        )
        self.assertTrue(
            grouping.field_links_to(member, "organizacion", "custodiosdejuutei", Ctx())
        )

    def test_a_plain_name_matches_too(self):
        # Not every vault writes the value as a wikilink.
        member = note("Briar", frontmatter={"organizacion": "Custodios de Juutei"})
        self.assertTrue(
            grouping.field_links_to(member, "organizacion", "custodiosdejuutei", Ctx())
        )

    def test_an_alias_resolves_through_the_note(self):
        """The link says one thing; the organisation's note is named another.

        Only the identity comparison can catch this, and it only works because
        the target note is handed in -- `find_note` casefolds and nothing more,
        so it could never resolve the folded term "custodiosdejuutei".
        """
        guild = note("Custodios de Juutei")
        ctx = Ctx({"Custodios de Juutei": guild, "Los Custodios": guild})
        member = note("Briar", frontmatter={"organizacion": "[[Los Custodios]]"})
        self.assertTrue(
            grouping.field_links_to(
                member, "organizacion", "custodiosdejuutei", ctx, guild
            )
        )

    def test_the_folded_term_alone_cannot_find_the_note(self):
        # Pins the reason for that argument: were the target looked up from the
        # term in here, the alias case above would silently stop working.
        guild = note("Custodios de Juutei")
        ctx = Ctx({"Custodios de Juutei": guild})
        self.assertIsNone(ctx.find_note("custodiosdejuutei"))
        self.assertIs(ctx.find_note("Custodios de Juutei"), guild)

    def test_a_different_organisation_does_not_match(self):
        member = note("Aronne", frontmatter={"organizacion": "[[Black Shawarma]]"})
        self.assertFalse(
            grouping.field_links_to(member, "organizacion", "custodiosdejuutei", Ctx())
        )

    def test_a_missing_key_does_not_match(self):
        self.assertFalse(
            grouping.field_links_to(note("Nobody"), "organizacion", "x", Ctx())
        )

    def test_a_non_string_value_is_ignored_rather_than_crashing(self):
        member = note("Odd", frontmatter={"organizacion": [None, 42, {"a": 1}]})
        self.assertFalse(
            grouping.field_links_to(member, "organizacion", "x", Ctx())
        )


class FenceOptions(unittest.TestCase):
    def test_an_empty_fence_is_all_defaults(self):
        self.assertEqual(i18n_query._parse_options(""), {})
        self.assertEqual(i18n_query._parse_options("\n  \n"), {})

    def test_the_three_keys_parse(self):
        got = i18n_query._parse_options("from: inquisidor\nnot: ex-inquisidor\nlayout: party")
        self.assertEqual(got, {"from": "inquisidor", "not": "ex-inquisidor", "layout": "party"})

    def test_quotes_and_comments_are_handled(self):
        got = i18n_query._parse_options('# a comment\nfrom: "Black Shawarma"\n')
        self.assertEqual(got, {"from": "Black Shawarma"})

    def test_an_unknown_key_is_reported_rather_than_ignored(self):
        # Silently dropping it would render a table that quietly ignores what
        # the author asked for, which is worse than saying so.
        message = i18n_query._parse_options("sort: title")
        self.assertIsInstance(message, str)
        self.assertIn("sort", message)

    def test_a_line_that_is_not_a_mapping_is_reported(self):
        message = i18n_query._parse_options("just some prose")
        self.assertIsInstance(message, str)
        self.assertIn("key: value", message)


class Markup(unittest.TestCase):
    """The DOM `Cards.css` keys off. A change here is a visual change."""

    def test_it_matches_the_dataview_table_exactly(self):
        got = grouping.render_table(["Retrato", "Nombre"], [["<img />", "<a>x</a>"]])
        self.assertEqual(
            got,
            '<table class="dataview table-view-table">'
            '<thead class="table-view-thead"><tr class="table-view-tr-header">'
            '<th class="table-view-th">Retrato</th>'
            '<th class="table-view-th">Nombre</th>'
            "</tr></thead>"
            '<tbody class="table-view-tbody">'
            "<tr><td><img /></td><td><a>x</a></td></tr>"
            "</tbody></table>",
        )

    def test_headers_are_escaped_and_cells_are_not(self):
        # Headers are plain text from the table; cells are already rendered.
        got = grouping.render_table(["A & B"], [['<a href="x">y</a>']])
        self.assertIn("<th class=\"table-view-th\">A &amp; B</th>", got)
        self.assertIn('<td><a href="x">y</a></td>', got)

    def test_no_members_still_renders_the_shape(self):
        got = grouping.render_table(["Nombre"], [])
        self.assertIn('<tbody class="table-view-tbody"></tbody>', got)

    def test_a_caption_sits_outside_the_grid(self):
        # Cards.css makes `tbody` a grid and every `tr` a card, so a section
        # label has to be a caption -- a header row would render as one more
        # card that looks like a member.
        got = grouping.render_table(["Nombre"], [], caption="Secundarios")
        self.assertIn('<caption class="table-view-caption">Secundarios</caption>', got)
        self.assertLess(got.index("<caption"), got.index("<thead"))

    def test_no_caption_emits_no_element(self):
        self.assertNotIn("<caption", grouping.render_table(["Nombre"], []))


class Sizes(unittest.TestCase):
    def test_the_two_forms_the_vault_uses(self):
        self.assertEqual(grouping.parse_size("500x500"), (500, 500))
        self.assertEqual(grouping.parse_size("250"), (250, None))

    def test_nothing_sensible_is_None(self):
        for value in (None, "", "big", "x500"):
            self.assertIsNone(grouping.parse_size(value), repr(value))


class TableGroups(unittest.TestCase):
    def test_a_group_is_found_by_NoteType(self):
        table = ib.InfoboxTable(
            layouts={"persona": {}},
            groups={"organizacion": {"columns": []}},
            available=True,
        )
        self.assertIsNotNone(table.group_for("organizacion"))
        self.assertIsNotNone(table.group_for("Organización"))
        self.assertIsNone(table.group_for("persona"))

    def test_a_table_with_no_groups_simply_has_none(self):
        # A vault that does not use the group box has a valid table, not a
        # broken one -- unlike `layouts`, which is required.
        table = ib.InfoboxTable(layouts={"persona": {}}, available=True)
        self.assertIsNone(table.group_for("organizacion"))



class Membership(unittest.TestCase):
    """The one axis on which two uses of the fence genuinely differ."""

    def test_a_roster_matches_by_tag_and_by_key(self):
        self.assertEqual(
            grouping.membership({"members": {"tag": True, "field": "organizacion"}}),
            (True, "organizacion"),
        )

    def test_a_chapter_list_matches_by_key_alone(self):
        self.assertEqual(
            grouping.membership({"members": {"tag": False, "field": "padre"}}),
            (False, "padre"),
        )

    def test_a_layout_that_says_nothing_is_tag_only(self):
        # Rather than silently inheriting `organizacion` from the first use
        # case this was written for.
        self.assertEqual(grouping.membership({}), (True, ""))
        self.assertEqual(grouping.membership({"members": "nonsense"}), (True, ""))


class Sorting(unittest.TestCase):
    def test_the_default_is_by_title(self):
        rows = [note("B", title="Beta"), note("A", title="Alfa")]
        grouping.sort_members(rows, None)
        self.assertEqual([n.title for n in rows], ["Alfa", "Beta"])

    def test_a_numeric_sort_reads_the_number_out_of_the_field(self):
        # What a chapter list needs: "10 - ..." must not sort before "2 - ...".
        rows = [
            note("10 - Legado", vault_path="P/10 - Legado.md"),
            note("2 - Turbulencias", vault_path="P/2 - Turbulencias.md"),
            note("1 - Intro", vault_path="P/1 - Intro.md"),
        ]
        grouping.sort_members(rows, {"by": "file.name", "numeric": True})
        self.assertEqual(
            [n.vault_path for n in rows],
            ["P/1 - Intro.md", "P/2 - Turbulencias.md", "P/10 - Legado.md"],
        )

    def test_desc_reverses_it(self):
        rows = [note("A", title="Alfa"), note("B", title="Beta")]
        grouping.sort_members(rows, {"by": "title", "desc": True})
        self.assertEqual([n.title for n in rows], ["Beta", "Alfa"])


class Sections(unittest.TestCase):
    """`sections:` -- as many labelled blocks per layout as the author wants."""

    def setUp(self):
        self.main = note("Moby", tags=["PJ"])
        self.second = note("Briar", tags=["secundario"])
        self.third = note("Gary", tags=["aliado"])
        self.rows = [self.main, self.second, self.third]

    def test_no_sections_is_one_untitled_table(self):
        # What every layout did before sections existed.
        self.assertEqual(grouping.partition(self.rows, None), [("", self.rows)])
        self.assertEqual(grouping.partition(self.rows, []), [("", self.rows)])

    def test_rows_land_in_the_section_whose_tag_they_carry(self):
        got = grouping.partition(
            self.rows,
            [{"label": "miembros"}, {"label": "secundarios", "tag": "secundario"}],
        )
        self.assertEqual([label for label, _ in got], ["miembros", "secundarios"])
        self.assertEqual(got[0][1], [self.main, self.third])
        self.assertEqual(got[1][1], [self.second])

    def test_as_many_sections_as_the_layout_declares(self):
        got = grouping.partition(
            self.rows,
            [
                {"label": "miembros"},
                {"label": "secundarios", "tag": "secundario"},
                {"label": "aliados", "tag": "aliado"},
            ],
        )
        self.assertEqual([label for label, _ in got], ["miembros", "secundarios", "aliados"])
        self.assertEqual([rows[0].title for _, rows in got], ["Moby", "Briar", "Gary"])

    def test_the_first_matching_section_wins(self):
        # A row carrying two section tags belongs to the earlier one, so the
        # order on the page is the order that decides.
        both = note("Both", tags=["secundario", "aliado"])
        got = grouping.partition(
            [both],
            [
                {"label": "m"},
                {"label": "s", "tag": "secundario"},
                {"label": "a", "tag": "aliado"},
            ],
        )
        self.assertEqual(got, [("s", [both])])

    def test_a_catch_all_is_added_when_every_section_names_a_tag(self):
        # Otherwise an untagged row would simply vanish -- the one failure a
        # reader could never notice.
        got = grouping.partition(self.rows, [{"label": "s", "tag": "secundario"}])
        self.assertEqual(got[0][0], "")
        self.assertEqual(got[0][1], [self.main, self.third])
        self.assertEqual(got[1], ("s", [self.second]))

    def test_the_catch_all_may_be_declared_last(self):
        got = grouping.partition(
            self.rows,
            [{"label": "s", "tag": "secundario"}, {"label": "resto"}],
        )
        self.assertEqual([label for label, _ in got], ["s", "resto"])

    def test_empty_sections_are_dropped(self):
        # A caption introducing nothing is worse than no caption.
        got = grouping.partition(
            [self.main], [{"label": "m"}, {"label": "s", "tag": "secundario"}]
        )
        self.assertEqual(got, [("m", [self.main])])

    def test_a_section_tag_folds_like_any_other_term(self):
        tagged = note("X", tags=["Ex-Miembro"])
        got = grouping.partition([tagged], [{"label": "m"}, {"label": "e", "tag": "ex miembro"}])
        self.assertEqual(got, [("e", [tagged])])


if __name__ == "__main__":
    unittest.main()
