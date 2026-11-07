"""Subset the site's webfonts and generate their @font-face rules.

The site sets its text in Inter, which is what the vault renders in: the theme's
`--font-default` lists display faces ahead of it that are not installed there
and are not shipped here, so Inter is the entry the stack actually resolves to.
`site-overrides.css` names it first to make that explicit rather than
incidental.

Inter is a *variable* font -- one file spans weights 100-900 -- so two files,
upright and italic, cover every weight the site uses. It is OFL-licensed; the
sources live in `vendor/fonts/`.

Inter has no Japanese, and this vault publishes Japanese: `i18n.ignore` keeps
English out, so a built site serves `es` and `ja`. Until Noto Sans JP was added
here, every Japanese page fell through the stack to whatever the reader's
operating system happened to supply -- so the one language whose typography we
had no control over was the one most likely to be set in an arbitrary font. The
site therefore ships two families, split by `unicode-range` so that a reader of
Spanish never downloads a byte of Japanese.

Run after adding or replacing a font source:

    .venv/Scripts/python scripts/build_fonts.py

Outputs `public/fonts/*.woff2` and `src/styles/fonts.css`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont


def span(lo: int, hi: int) -> set[int]:
    return set(range(lo, hi + 1))


# --- Repertoires -----------------------------------------------------------
#
# A subset is a set of codepoints to keep plus the `unicode-range` that
# advertises it. The two need not agree exactly, and for Japanese they
# deliberately do not: the range names whole blocks, the file holds a chosen
# part of them, and a codepoint the file is missing falls through to the next
# family in the stack, per character. Declaring a block is a download hint, not
# a promise to cover it.

# Kept ranges, and why each is needed for *this* vault:
#   Basic Latin + Latin-1     Spanish: a e i o u u n ? !  (accented)
#   Latin Extended-A          Esperanto: s g c j h u with diacritics
#   General Punctuation       curly quotes, en/em dashes, ellipsis
#   Box Drawing               the infobox templates' tree characters
#   Geometric Shapes          the folder-note marker in the sidebar
LATIN_RANGES = [
    (0x0020, 0x007E),
    (0x00A0, 0x00FF),
    (0x0100, 0x017F),
    (0x0192, 0x0192),
    (0x2000, 0x206F),
    (0x20A0, 0x20BF),
    (0x2190, 0x2193),
    (0x2500, 0x257F),
    (0x25A0, 0x25FF),
    (0xFB00, 0xFB06),
]

LATIN_CSS = (
    "U+0020-007E, U+00A0-00FF, U+0100-017F, U+0192, U+2000-206F, "
    "U+20A0-20BF, U+2190-2193, U+2500-257F, U+25A0-25FF, U+FB00-FB06"
)

# Kana, CJK punctuation and the fullwidth forms: the parts of written Japanese
# that are a fixed, small inventory. Kanji are the open-ended part, chosen
# separately below.
KANA_AND_PUNCTUATION = (
    span(0x3000, 0x30FF)  # CJK punctuation, hiragana, katakana
    | span(0x31F0, 0x31FF)  # katakana phonetic extensions
    | span(0xFF01, 0xFF60)  # fullwidth ASCII
    | span(0xFF61, 0xFF9F)  # halfwidth katakana
    | span(0xFFE0, 0xFFE6)  # fullwidth symbols
)

# The Japanese range stops short of the punctuation Inter already carries
# (U+2000-206F and below). Both families are in the stack with Inter first, so
# an ellipsis or an em dash resolves to Inter and the Japanese face is never
# requested for a page of Spanish prose that happens to contain one.
JAPANESE_CSS = (
    "U+3000-303F, U+3040-309F, U+30A0-30FF, U+31F0-31FF, "
    "U+4E00-9FFF, U+FF00-FFEF"
)


def jis_x0208_kanji(level_1_only: bool = False) -> set[int]:
    """The kanji of JIS X 0208, read off a codec rather than a vendored list.

    A kanji subset has to be chosen, because the Unified Ideographs block holds
    20,992 characters and nobody wants to serve them all. JIS X 0208 is what
    "written Japanese" has meant since 1978 -- 6,355 kanji -- and Python's
    `shift_jis` codec maps precisely that repertoire, so membership is a test
    rather than a list this repository would have to keep in step by hand.

    The standard splits into level 1 (2,965 kanji, rows 16-47, ordered by
    reading) and level 2 (the remaining 3,390, ordered by radical: rarer
    characters, and the ones that turn up in surnames and place names). EUC-JP
    encodes row *r* as first byte 0xA0+r, so the level is likewise readable
    straight off an encoding.

    Both are offered because the choice is a real trade-off. Measured on this
    source, as woff2:

        kana + punctuation only            438 glyphs     200 KB
        + JIS X 0208 level 1             3,403 glyphs   1,089 KB
        + JIS X 0208 levels 1 and 2      6,794 glyphs   2,091 KB

    The site ships the full standard. Level 1 covers every kanji currently in
    the vault, but it misses name kanji, and a character's own name rendering
    in a different font from the sentence around it is the one failure a reader
    cannot help noticing. A megabyte, once, cached, and only for readers who
    actually read Japanese, is the cheaper side of that trade.
    """
    kanji: set[int] = set()
    for codepoint in span(0x4E00, 0x9FFF):
        try:
            chr(codepoint).encode("shift_jis")
        except UnicodeEncodeError:
            continue  # outside JIS X 0208
        if level_1_only:
            # Anything still here is in one level or the other, so the row byte
            # decides which.
            row_byte = chr(codepoint).encode("euc_jp")[0]
            if not 0xB0 <= row_byte <= 0xCF:
                continue
        kanji.add(codepoint)
    return kanji


# The language switcher's label is the only Japanese the *interface* sets, and
# it renders on every multilingual page whether or not the reader has switched
# to Japanese. Served from the content face, three characters in a button would
# pull two megabytes down for somebody reading Spanish, so they get a face of
# their own instead -- a few kilobytes, used by `.lang-pill` alone.
#
# Keep in step with `LANGUAGES` in src/components/LanguageBar.astro, the same
# way that file is kept in step with `i18n.languages` in vault.config.yaml.
UI_JAPANESE = {ord(c) for c in "日本語"}  # the label for Japanese

SUBSETS: dict[str, dict] = {
    "latin": {
        "codepoints": lambda: {c for lo, hi in LATIN_RANGES for c in span(lo, hi)},
        "css_range": LATIN_CSS,
    },
    "japanese": {
        "codepoints": lambda: KANA_AND_PUNCTUATION | jis_x0208_kanji(),
        "css_range": JAPANESE_CSS,
    },
    "japanese-ui": {
        "codepoints": lambda: UI_JAPANESE,
        # Exactly what it holds: nothing else should ever reach for this face.
        "css_range": "U+65E5, U+672C, U+8A9E",
    },
}

# --- Families --------------------------------------------------------------
#
# Each face is (source filename, output stem, css font-weight, css font-style).
# A variable font declares a weight *range*, which is why Inter needs only two
# files where a static family needs one per weight. Two families may read the
# same source and subset it differently, which is how the interface face and
# the content face both come out of one Noto Sans JP.

FAMILIES: dict[str, dict] = {
    "inter": {
        "css_family": "Inter",
        "subset": "latin",
        "notice": [
            "Inter is (c) The Inter Project Authors, licensed under the SIL",
            "Open Font License 1.1 -- https://github.com/rsms/inter",
        ],
        "faces": [
            ("Inter-Variable.woff2", "inter-variable", "100 900", "normal"),
            ("Inter-Variable-Italic.woff2", "inter-variable-italic", "100 900", "italic"),
        ],
    },
    "noto-sans-jp": {
        "css_family": "Noto Sans JP",
        "subset": "japanese",
        "notice": [
            "Noto Sans JP is (c) The Noto Project Authors, licensed under the",
            "SIL Open Font License 1.1 -- https://github.com/notofonts/noto-cjk",
            "Japanese has no true italic, so there is no italic face to ship:",
            "an italic run takes Inter for its Latin and the upright Japanese",
            "face for the rest, which is what the language expects.",
        ],
        "faces": [
            ("NotoSansJP-Variable.woff2", "noto-sans-jp-variable", "100 900", "normal"),
        ],
    },
    "noto-sans-jp-ui": {
        "css_family": "Noto Sans JP UI",
        "subset": "japanese-ui",
        "notice": [
            "The same Noto Sans JP, cut down to the language switcher's label.",
            "See UI_JAPANESE in scripts/build_fonts.py for why it is separate.",
        ],
        "faces": [
            ("NotoSansJP-Variable.woff2", "noto-sans-jp-ui", "100 900", "normal"),
        ],
    },
}

# What a built site ships, in the order `--font-default` names them. Order is
# what resolves a codepoint both families claim: Inter comes first, so it keeps
# the punctuation it has always set.
SITE = ("inter", "noto-sans-jp", "noto-sans-jp-ui")


def build_face(source: Path, stem: str, keep: list[int], out_dir: Path) -> tuple[int, int]:
    """Subset and convert one face. Returns (source bytes, output bytes)."""
    before = source.stat().st_size

    # `recalcTimestamp=False` keeps the source's `head.modified` instead of
    # stamping "now". Without it every run produces different bytes, and the
    # generated fonts show up as a spurious diff on each build.
    font = TTFont(str(source), recalcTimestamp=False)

    options = subset.Options()
    options.layout_features = ["*"]  # keep kerning and ligatures
    options.desubroutinize = True
    options.hinting = True
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recalc_timestamp = False
    # Drop name/metadata tables that only matter to desktop font managers --
    # but keep 13 and 14, the embedded licence and its URL. Both families are
    # OFL, and a licence that travels with the file it covers is worth the two
    # hundred bytes it costs.
    options.name_IDs = [1, 2, 3, 4, 6, 13, 14]
    options.drop_tables += ["DSIG"]

    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=keep)
    subsetter.subset(font)

    font.flavor = "woff2"
    destination = out_dir / f"{stem}.woff2"
    font.save(str(destination))
    font.close()

    return before, destination.stat().st_size


def render_css(families: list[str], faces: dict[str, list[tuple[str, str, str]]]) -> str:
    lines = [
        "/* GENERATED by scripts/build_fonts.py -- do not edit.",
        " * Sources in vendor/fonts/, subset to the scripts this vault writes",
        " * in. Must load BEFORE the theme so its `--font-default` stack",
        " * resolves to a real face.",
        " *",
        " * Subsetting and WOFF2 conversion do not change any font's terms.",
        " */",
        "",
    ]

    for family in families:
        spec = FAMILIES[family]
        name = spec["css_family"]
        css_range = SUBSETS[spec["subset"]]["css_range"]

        lines.append(f"/* {name} -- subset `{spec['subset']}`.")
        for note in spec["notice"]:
            lines.append(f" * {note}")
        lines.append(" */")

        for stem, weight, style in faces[family]:
            lines.append(
                "@font-face {\n"
                f'  font-family: "{name}";\n'
                f'  src: url("/fonts/{stem}.woff2") format("woff2");\n'
                f"  font-weight: {weight};\n"
                f"  font-style: {style};\n"
                "  font-display: swap;\n"
                f"  unicode-range: {css_range};\n"
                "}"
            )
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="vendor/fonts")
    parser.add_argument("--out", default="public/fonts")
    parser.add_argument("--css", default="src/styles/fonts.css")
    parser.add_argument(
        "--family",
        action="append",
        choices=sorted(FAMILIES),
        help=(
            "build only this family; repeatable. Defaults to everything the "
            "site ships. Narrowing the build also disables pruning, so a "
            "one-off rebuild of one family cannot delete another's output."
        ),
    )
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    root = Path(__file__).resolve().parents[1]
    source_dir = root / args.source
    out_dir = root / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    narrowed = args.family is not None
    if narrowed:
        # Keep SITE's order for the families that are in it, then anything
        # asked for that the site does not ship.
        selected = [f for f in SITE if f in args.family]
        selected += [f for f in args.family if f not in SITE]
    else:
        selected = list(SITE)

    built: dict[str, list[tuple[str, str, str]]] = {}
    order: list[str] = []
    total_before = total_after = 0

    for family in selected:
        spec = FAMILIES[family]
        subset_name = spec["subset"]
        wanted = SUBSETS[subset_name]["codepoints"]()
        print(f"{family}  (subset `{subset_name}`, {len(wanted):,} codepoints wanted)")

        for filename, stem, weight, style in spec["faces"]:
            source = source_dir / filename
            if not source.exists():
                print(f"  MISSING {filename}", file=sys.stderr)
                continue

            font = TTFont(str(source), recalcTimestamp=False)
            available = set(font.getBestCmap())
            font.close()
            keep = sorted(available & wanted)

            before, after = build_face(source, stem, keep, out_dir)
            total_before += before
            total_after += after
            built.setdefault(family, []).append((stem, weight, style))
            if family not in order:
                order.append(family)
            print(f"  {filename:30} {before:>9,} -> {after:>9,} B  ({len(keep):,} glyphs)")
        print()

    if not built:
        print("no faces built", file=sys.stderr)
        return 1

    (root / args.css).write_text(
        render_css(order, built), encoding="utf-8", newline="\n"
    )

    # Stale output from a family the site no longer ships would otherwise linger
    # and be served for no reason. Only a full build knows what the full set is,
    # so a narrowed build does not prune.
    removed = 0
    if not narrowed:
        expected = {f"{stem}.woff2" for faces in built.values() for stem, _, _ in faces}
        for path in sorted(out_dir.glob("*.woff2")):
            if path.name not in expected:
                path.unlink()
                removed += 1

    print(f"  total {total_before:,} -> {total_after:,} bytes")
    print(f"  wrote {args.css} and {sum(len(f) for f in built.values())} woff2 file(s)")
    if removed:
        print(f"  pruned {removed} file(s) no longer shipped")
    elif narrowed:
        print("  pruning skipped: the build was narrowed with --family")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
