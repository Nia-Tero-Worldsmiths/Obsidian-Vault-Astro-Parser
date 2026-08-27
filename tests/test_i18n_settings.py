"""`i18n.languages` vs `i18n.ignore`.

`languages` mirrors what the vault and the plugin can author in. `ignore`
subtracts what the site does not publish yet. Keeping them apart is the whole
point: a `:::lang en` block in a vault that supports English is a deliberate
draft, one in a vault that does not is a typo, and a single list cannot tell
those apart.
"""

from __future__ import annotations

import unittest

from scripts.vault_parser import langblocks as lb
from scripts.vault_parser.config import ConfigError, I18nSettings, Language, _parse_i18n


def settings(**raw) -> I18nSettings:
    raw.setdefault("enabled", True)
    raw.setdefault("default", "es")
    raw.setdefault("languages", [{"code": "es"}, {"code": "ja"}, {"code": "en"}])
    return _parse_i18n(raw)


class Served(unittest.TestCase):
    def test_nothing_ignored_serves_every_language(self):
        s = settings()
        self.assertEqual(s.codes, ("es", "ja", "en"))
        self.assertEqual(s.served, ("es", "ja", "en"))

    def test_an_ignored_language_is_still_a_known_code(self):
        s = settings(ignore=["en"])
        self.assertEqual(s.served, ("es", "ja"))
        self.assertIn("en", s.codes, "the vault still supports it")
        self.assertTrue(s.is_ignored("en"))
        self.assertFalse(s.is_ignored("ja"))

    def test_served_keeps_configured_order(self):
        s = settings(ignore=["ja"])
        self.assertEqual(s.served, ("es", "en"))

    def test_matching_is_case_insensitive(self):
        self.assertEqual(settings(ignore=["EN"]).served, ("es", "ja"))
        self.assertTrue(settings(ignore=["en"]).is_ignored("EN"))


class Guards(unittest.TestCase):
    def test_ignoring_the_default_is_refused(self):
        # Every note falls back to the default; ignoring it would empty the site.
        with self.assertRaises(ConfigError) as caught:
            settings(ignore=["es"])
        self.assertIn("default", str(caught.exception))

    def test_ignoring_an_undeclared_code_is_refused(self):
        # Almost always a typo, and silently doing nothing would hide it.
        with self.assertRaises(ConfigError) as caught:
            settings(ignore=["de"])
        self.assertIn("de", str(caught.exception))


class Expansion(unittest.TestCase):
    """`:::lang all` must expand to the served set, not the supported one.

    This is the whole reason a note sprouted an English section holding
    nothing but its shared content.
    """

    def test_all_expands_only_to_served_languages(self):
        blocks = lb.parse_blocks(":::lang all" + chr(10) + "x" + chr(10) + ":::")
        served = settings(ignore=["en"]).served
        self.assertEqual(lb.available_languages(blocks, served), ["es", "ja"])

    def test_a_block_only_in_an_ignored_language_yields_nothing(self):
        blocks = lb.parse_blocks(":::lang en" + chr(10) + "x" + chr(10) + ":::")
        served = settings(ignore=["en"]).served
        self.assertEqual(lb.available_languages(blocks, served), [])

    def test_a_multi_code_block_survives_through_its_served_code(self):
        blocks = lb.parse_blocks(":::lang es en" + chr(10) + "x" + chr(10) + ":::")
        served = settings(ignore=["en"]).served
        self.assertEqual(lb.available_languages(blocks, served), ["es"])

    def test_ignored_content_is_dropped_from_the_body(self):
        source = (
            ":::lang es" + chr(10) + "Español." + chr(10) + ":::" + chr(10)
            + ":::lang en" + chr(10) + "English." + chr(10) + ":::"
        )
        bodies = lb.split_bodies(source, ["es", "ja"], "es")
        self.assertIn("Español.", bodies["es"])
        self.assertNotIn("English.", bodies["es"])
        self.assertNotIn("English.", bodies["ja"])


if __name__ == "__main__":
    unittest.main()
