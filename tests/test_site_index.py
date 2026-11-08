"""`site.json`: the language list the site-authored pages read.

`/`, `/galeria` and `/grafo` have no note behind them, so they cannot learn
their languages the way every other page does. This module is where they get
them, and the thing worth pinning down is which list it writes: `served`, not
`codes`. Offering a pill for a language the parser never emitted is a dead
end for the reader and looks like a broken page rather than a stale constant.
"""

from __future__ import annotations

import unittest

from scripts.vault_parser.config import _parse_i18n
from scripts.vault_parser.modules.site_index import SiteIndexModule


class Config:
    def __init__(self, i18n) -> None:
        self.i18n = i18n


class Ctx:
    """Enough context for `finalize`, which reads config and nothing else."""

    def __init__(self, i18n) -> None:
        self.config = Config(i18n)
        self.generated: dict = {}


def settings(**raw):
    raw.setdefault("enabled", True)
    raw.setdefault("default", "es")
    raw.setdefault(
        "languages",
        [
            {"code": "es", "label": "Español"},
            {"code": "ja", "label": "日本語"},
            {"code": "en", "label": "English"},
        ],
    )
    return _parse_i18n(raw)


def run(**raw):
    module = SiteIndexModule(enabled=True)
    ctx = Ctx(settings(**raw))
    module.finalize(ctx)
    return ctx.generated[SiteIndexModule.ARTIFACT], module


class Artifact(unittest.TestCase):
    def test_every_served_language_is_written_with_its_label(self):
        data, _ = run()
        self.assertEqual(
            data["languages"],
            [
                {"code": "es", "label": "Español"},
                {"code": "ja", "label": "日本語"},
                {"code": "en", "label": "English"},
            ],
        )

    def test_the_default_language_is_named(self):
        data, _ = run()
        self.assertEqual(data["defaultLanguage"], "es")

    def test_configured_order_is_kept(self):
        # The language bar renders these in order, so this is what decides
        # which pill sits first.
        data, _ = run(languages=[{"code": "ja"}, {"code": "es"}], default="es")
        self.assertEqual([lang["code"] for lang in data["languages"]], ["ja", "es"])

    def test_a_language_with_no_label_falls_back_to_its_code(self):
        data, _ = run(languages=[{"code": "es"}, {"code": "eo"}])
        self.assertEqual(data["languages"][1], {"code": "eo", "label": "eo"})


class Ignored(unittest.TestCase):
    """The distinction the whole module exists to preserve."""

    def test_an_ignored_language_is_not_offered(self):
        data, _ = run(ignore=["en"])
        self.assertEqual([lang["code"] for lang in data["languages"]], ["es", "ja"])

    def test_ignoring_everything_but_the_default_leaves_one_language(self):
        # The bar hides itself below two, so this is the monolingual site.
        data, _ = run(ignore=["ja", "en"])
        self.assertEqual([lang["code"] for lang in data["languages"]], ["es"])

    def test_withheld_languages_are_reported(self):
        _, module = run(ignore=["en"])
        body = "\n".join(line for section in module.stats.sections for line in section[1])
        self.assertIn("en", body)

    def test_nothing_is_reported_when_nothing_is_withheld(self):
        _, module = run()
        self.assertEqual(module.stats.sections, [])


if __name__ == "__main__":
    unittest.main()
