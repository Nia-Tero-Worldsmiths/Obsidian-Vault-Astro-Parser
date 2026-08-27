"""Ported from the plugin's `tests/syntaxConsistency.test.ts`, plus the rules
that only exist in `markdownProcessor.ts`.

The negative half matters more than the positive half. `%%` is ordinary
Obsidian comment syntax and is used ~254 times in this vault; the upstream
plugin accepted `%% lang es %%` as a marker, and the fork exists largely to
undo that. If any of these start parsing as blocks, notes lose content
silently.
"""

from __future__ import annotations

import unittest

from scripts.vault_parser import langblocks as lb

CONFIGURED = ("es", "ja", "en")


def codes(source: str) -> list[str]:
    return [block.codes for block in lb.parse_blocks(source)]


class FencedDivStyle(unittest.TestCase):
    def test_a_paired_block_parses(self):
        [block] = lb.parse_blocks(":::lang es\nHola.\n:::")
        self.assertEqual(block.codes, "es")
        self.assertEqual((block.open_line, block.close_line), (0, 2))
        self.assertFalse(block.is_open_ended)

    def test_a_hyphenated_code_is_preserved(self):
        self.assertEqual(codes(":::lang zh-CN\nx\n:::"), ["zh-CN"])

    def test_multiple_codes_are_kept_as_authored(self):
        self.assertEqual(codes(":::lang es en\nx\n:::"), ["es en"])

    def test_all_is_captured_like_any_other_code(self):
        self.assertEqual(codes(":::lang all\nx\n:::"), ["all"])

    def test_two_sibling_blocks_parse_independently_in_order(self):
        source = ":::lang es\na\n:::\n\n:::lang ja\nb\n:::"
        self.assertEqual(codes(source), ["es", "ja"])

    def test_an_unclosed_block_runs_to_end_of_file(self):
        [block] = lb.parse_blocks(":::lang es\na\nb")
        self.assertTrue(block.is_open_ended)
        self.assertTrue(block.contains(2))

    def test_blocks_never_nest(self):
        # While one block is open only a close marker is looked for, so the
        # inner opener stays literal text and the first `:::` closes the outer.
        source = ":::lang es\n:::lang ja\ninner\n:::\nafter"
        [block] = lb.parse_blocks(source)
        self.assertEqual(block.codes, "es")
        self.assertEqual(block.close_line, 3)


class RejectedSyntax(unittest.TestCase):
    """None of these may ever produce a block."""

    def test_percent_comment_markers_are_inert(self):
        for source in (
            "%% lang zh-CN %%\nx\n%% endlang %%",
            "%% lang es %%\nx\n%% ::: %%",
            "%%lang es%%\nx\n%%endlang%%",
        ):
            self.assertEqual(lb.parse_blocks(source), [], source)

    def test_the_vault_s_own_comment_shapes_are_inert(self):
        for source in (
            "%%### Historia%%",
            "%%Alt+M para crear un mapa aquí%%",
            "%% lang es %%",
            "%% endlang %%",
        ):
            self.assertEqual(lb.parse_blocks(source), [], source)

    def test_the_other_upstream_styles_are_inert(self):
        for source in (
            "{% lang es %}\nx\n{% endlang %}",
            "[//]: # (lang es)\nx\n[//]: # (endlang)",
            "[//]: # (:::)",
        ):
            self.assertEqual(lb.parse_blocks(source), [], source)

    def test_an_open_marker_needs_a_code(self):
        self.assertEqual(lb.parse_blocks(":::lang\nx\n:::"), [])
        self.assertEqual(lb.parse_blocks(":::lang \nx\n:::"), [])

    def test_a_bare_close_is_never_an_open(self):
        self.assertEqual(lb.parse_blocks(":::\nx\n:::"), [])

    def test_four_colons_are_neither(self):
        self.assertEqual(lb.parse_blocks("::::lang es\nx\n::::"), [])

    def test_a_note_with_no_markers_has_no_blocks(self):
        self.assertEqual(lb.parse_blocks("# Title\n\nJust prose.\n"), [])


class ColumnZero(unittest.TestCase):
    """Markers must start at column 0, as a *line* rule.

    `matchLanguageBlockOpen` trims before matching, which is right for the
    plugin's DOM stripper -- rendered text nodes carry their own whitespace --
    but as a line rule it would also match a marker indented inside a list item
    or a four-space code block. The plugin gates on `isMarkerLine` for exactly
    this reason.
    """

    def test_an_indented_open_marker_is_not_a_marker(self):
        self.assertEqual(lb.parse_blocks("  :::lang es\nx\n:::"), [])
        self.assertEqual(lb.parse_blocks("\t:::lang es\nx\n:::"), [])

    def test_an_indented_close_does_not_close(self):
        [block] = lb.parse_blocks(":::lang es\nx\n  :::")
        self.assertTrue(block.is_open_ended)

    def test_trailing_whitespace_is_still_tolerated(self):
        self.assertEqual(codes(":::lang es   \nx\n:::  "), ["es"])

    def test_internal_spacing_is_tolerated(self):
        self.assertEqual(codes("::: lang   es\nx\n:::"), ["es"])


class FencedCodeIsInert(unittest.TestCase):
    """A fence that *documents* the syntax must not be parsed as markup.

    The vault's own `_i18n-test.md` contains exactly this, and under the older
    plugin it made its own code sample vanish on a language switch.
    """

    def test_a_documenting_fence_produces_no_blocks(self):
        source = "```markdown\n:::lang es\nSample.\n:::\n```"
        self.assertEqual(lb.parse_blocks(source), [])

    def test_a_tilde_fence_is_equally_inert(self):
        source = "~~~markdown\n:::lang es\nSample.\n:::\n~~~"
        self.assertEqual(lb.parse_blocks(source), [])

    def test_a_shorter_run_does_not_close_a_longer_fence(self):
        source = "````\n```\n:::lang es\nx\n:::\n```\n````"
        self.assertEqual(lb.parse_blocks(source), [])

    def test_a_backtick_run_does_not_close_a_tilde_fence(self):
        source = "~~~\n```\n:::lang es\nx\n:::\n```\n~~~"
        self.assertEqual(lb.parse_blocks(source), [])

    def test_a_closing_fence_may_not_carry_an_info_string(self):
        # ```markdown ... ```js ... ``` is one fence, not two.
        source = "```markdown\n:::lang es\n```js\nx\n:::\n```"
        self.assertEqual(lb.parse_blocks(source), [])

    def test_a_real_block_after_a_fence_still_parses(self):
        source = "```\n:::lang es\n```\n\n:::lang ja\nreal\n:::"
        self.assertEqual(codes(source), ["ja"])


class Matching(unittest.TestCase):
    def test_lang_match_is_case_insensitive(self):
        self.assertTrue(lb.lang_match("es", "ES"))
        self.assertTrue(lb.lang_match("ES EN", "en"))
        self.assertFalse(lb.lang_match("es en", "ja"))

    def test_all_shows_under_every_language(self):
        self.assertTrue(lb.lang_match("all", "ja"))
        self.assertTrue(lb.lang_match("ALL", "ja"))

    def test_the_all_sentinel_shows_everything(self):
        self.assertTrue(lb.lang_match("es", lb.ALL))
        self.assertTrue(lb.lang_match("ja", lb.ALL))

    def test_available_languages_follows_configured_order(self):
        blocks = lb.parse_blocks(":::lang en\na\n:::\n:::lang es\nb\n:::")
        self.assertEqual(lb.available_languages(blocks, CONFIGURED), ["es", "en"])

    def test_a_block_tagged_all_counts_as_every_language(self):
        blocks = lb.parse_blocks(":::lang all\na\n:::")
        self.assertEqual(lb.available_languages(blocks, CONFIGURED), list(CONFIGURED))


class Splitting(unittest.TestCase):
    def split(self, source, langs=("es", "ja", "en")):
        return lb.split_bodies(source, list(langs), "es")

    def test_a_note_with_no_markers_is_default_language(self):
        bodies = self.split("Just prose.")
        self.assertEqual(bodies["es"], "Just prose.")

    def test_content_above_the_first_marker_is_shared(self):
        bodies = self.split("Shared intro.\n\n:::lang es\nOnly es.\n:::")
        for code in ("es", "ja", "en"):
            self.assertIn("Shared intro.", bodies[code])
        self.assertNotIn("Only es.", bodies["ja"])

    def test_content_between_blocks_is_default_language_only(self):
        # The threshold is a single line number, not "after the last block".
        source = ":::lang es\na\n:::\n\nStranded.\n\n:::lang ja\nb\n:::"
        bodies = self.split(source)
        self.assertIn("Stranded.", bodies["es"])
        self.assertNotIn("Stranded.", bodies["ja"])

    def test_markers_never_survive_into_any_output(self):
        bodies = self.split(":::lang es\na\n:::\n:::lang ja\nb\n:::")
        for code, text in bodies.items():
            self.assertNotIn(":::", text, code)

    def test_an_all_block_reaches_every_language(self):
        bodies = self.split("x\n\n:::lang all\nShared.\n:::")
        for code in ("es", "ja", "en"):
            self.assertIn("Shared.", bodies[code])

    def test_a_multi_code_block_reaches_exactly_those_languages(self):
        bodies = self.split("x\n\n:::lang es en\nBoth.\n:::")
        self.assertIn("Both.", bodies["es"])
        self.assertIn("Both.", bodies["en"])
        self.assertNotIn("Both.", bodies["ja"])

    def test_an_unwrapped_infobox_is_shared(self):
        # It re-renders per language on its own, so hiding it as
        # "default-language content" would only ever be surprising.
        source = ":::lang es\na\n:::\n\n```i18n-infobox\n```\n\n:::lang ja\nb\n:::"
        bodies = self.split(source)
        for code in ("es", "ja", "en"):
            self.assertIn("i18n-infobox", bodies[code], code)

    def test_block_codes_named_like_a_sentinel_do_not_collide(self):
        bodies = lb.split_bodies(":::lang drop\nx\n:::", ["drop", "es"], "es")
        self.assertEqual(bodies["drop"], "x")
        self.assertEqual(bodies["es"], "")


if __name__ == "__main__":
    unittest.main()
