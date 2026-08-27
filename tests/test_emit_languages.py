"""How the per-language bodies are merged into one document.

The rule these pin down: content identical in every language is emitted once,
unwrapped; only what differs gets a `.lang-section` wrapper. That is what stops
a `:::lang all` map being emitted three times, which made pan/zoom per-language
and left the hidden copies initialising at the wrong scale.
"""

from __future__ import annotations

import unittest

from scripts.vault_parser import emit
from scripts.vault_parser.model import Note

NL = chr(10)


def note(bodies: dict[str, str], languages: list[str] | None = None) -> Note:
    codes = languages or list(bodies)
    return Note(
        vault_path="x.md",
        source=__import__("pathlib").Path("x.md"),
        collection="atlas",
        slug="x",
        title="X",
        breadcrumb=[],
        lang_bodies=dict(bodies),
        languages=codes,
        body=bodies[codes[0]],
    )


class Merging(unittest.TestCase):
    def test_a_monolingual_note_gets_no_wrapper_at_all(self):
        n = note({"es": "Solo español."})
        self.assertEqual(emit._body(n, "es"), "Solo español.")
        self.assertNotIn("lang-section", emit._body(n, "es"))

    def test_an_identical_block_is_emitted_once_and_unwrapped(self):
        shared = "<figure class=\"zoommap\"></figure>"
        n = note({
            "es": "## Mapa" + NL * 2 + shared,
            "ja": "## 地図" + NL * 2 + shared,
        })
        out = emit._body(n, "es")
        self.assertEqual(out.count(shared), 1, "the map must appear exactly once")
        # ...and outside every language wrapper, so every language sees it: by
        # the point the shared block appears, every wrapper opened before it
        # has been closed again.
        before = out.partition(shared)[0]
        self.assertEqual(
            before.count('<div class="lang-section"'),
            before.count("</div>"),
            "the shared block must not sit inside a language wrapper",
        )
        self.assertIn("## Mapa", out)
        self.assertIn("## 地図", out)

    def test_blocks_that_differ_stay_wrapped_per_language(self):
        n = note({"es": "Hola.", "ja": "こんにちは。"})
        out = emit._body(n, "es")
        self.assertIn('<div class="lang-section" data-lang="es">', out)
        self.assertIn('<div class="lang-section" data-lang="ja">', out)

    def test_sharing_never_splits_a_multi_line_construct(self):
        """A line-level diff would share the wrapper and nest the rows inside.

        The infobox opens and closes with lines identical in every language
        while the rows between them differ. Sharing must be all-or-nothing.
        """
        def box(label):
            return ('<div class="ml-infobox-host">' + NL
                    + '<div class="callout ml-infobox">' + NL
                    + f"<td>{label}</td>" + NL
                    + "</div>" + NL + "</div>")

        n = note({"es": box("Ubicado en"), "ja": box("所在地")})
        out = emit._body(n, "es")
        # Each infobox is whole, inside exactly one language section.
        for lang, label in (("es", "Ubicado en"), ("ja", "所在地")):
            marker = f'<div class="lang-section" data-lang="{lang}">'
            segment = out.split(marker, 1)[1].split("</div>" + NL * 2 + '<div class="lang-section"')[0]
            self.assertIn('<div class="ml-infobox-host">', segment)
            self.assertIn(label, segment)
        # The wrapper was never hoisted out on its own.
        self.assertEqual(out.count('<div class="ml-infobox-host">'), 2)

    def test_a_fence_containing_blank_lines_is_not_split(self):
        fence = "```text" + NL + "a" + NL + NL + "b" + NL + "```"
        n = note({"es": fence + NL * 2 + "Español.", "ja": fence + NL * 2 + "日本語。"})
        out = emit._body(n, "es")
        self.assertEqual(out.count("```text"), 1)
        self.assertEqual(out.count("```"), 2, "the fence must stay balanced")

    def test_shared_and_unique_content_keeps_document_order(self):
        n = note({
            "es": "UNO-es" + NL * 2 + "SHARED" + NL * 2 + "TRES-es",
            "ja": "UNO-ja" + NL * 2 + "SHARED" + NL * 2 + "TRES-ja",
        })
        out = emit._body(n, "es")
        self.assertLess(out.index("UNO-es"), out.index("SHARED"))
        self.assertLess(out.index("SHARED"), out.index("TRES-es"))
        self.assertEqual(out.count("SHARED"), 1)

    def test_nothing_in_common_falls_back_to_whole_bodies(self):
        n = note({"es": "A", "ja": "B"})
        out = emit._body(n, "es")
        self.assertIn("A", out)
        self.assertIn("B", out)
        self.assertEqual(out.count('<div class="lang-section"'), 2)

    def test_a_language_with_nothing_to_say_gets_no_empty_wrapper(self):
        shared = "SHARED"
        n = note({"es": "Español." + NL * 2 + shared, "ja": shared})
        out = emit._body(n, "es")
        self.assertNotIn('data-lang="ja"', out, "ja adds nothing beyond the shared block")
        self.assertEqual(out.count(shared), 1)


if __name__ == "__main__":
    unittest.main()
