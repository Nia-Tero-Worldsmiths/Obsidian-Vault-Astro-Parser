# Nia Tero Exxet

A static-site generator for Obsidian vaults. A Python pipeline reads a vault
read-only and emits Astro content collections; Astro builds a wiki from them.

It was written for one vault — the world of Nia Tero — and still assumes that
vault's conventions in places, most visibly that the site reproduces the
vault's own Obsidian theme rather than defining a look of its own. Those
assumptions are documented where they bite.

## This repository holds the tool, not the site

**No vault content is tracked here.** Not the notes, not the images, not the
generated stylesheet — those all belong to a particular vault, and this
repository is meant to be readable without it.

The line is: *track what does not depend on a vault*. It falls neatly between
the two generator scripts. `build_fonts.py` reads `vendor/fonts/`, so it and
its output are committed. `extract_theme.py` reads the vault's own theme, so
the script is committed and its output is not.

| ignored | why |
|---|---|
| `src/content/`, `src/generated/` | parser output |
| `public/vault-assets/` | images copied out of a vault |
| `src/styles/vault-theme.css` | generated from the vault's theme; also the only GPL-2.0 file, so keeping it out means this repository distributes no copyleft |
| `vault.config.yaml` | names a vault path particular to one machine |

A fresh clone therefore **cannot build the site** — there is nothing to build
until you point it at a vault. `examples/example-vault/` exists so you can do
that immediately.

## Setup

Python for the parser, Node 22.12 or newer for the site.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r scripts/requirements.txt
npm install
cp vault.config.example.yaml vault.config.yaml
```

Then set `vault_root` in that copy. To try it without a vault of your own,
point it at `examples/example-vault` — the collection names already match.

## Running

```bash
.venv/Scripts/python -m scripts.vault_parser --dry-run --verbose   # writes nothing
.venv/Scripts/python -m scripts.vault_parser                       # emit content
npm run dev                                                        # serve at :4321
npm run build                                                      # build to dist/
```

Two stylesheets are generated separately, and only when their sources change:

```bash
.venv/Scripts/python scripts/build_fonts.py     # public/fonts/, src/styles/fonts.css
.venv/Scripts/python scripts/extract_theme.py   # src/styles/vault-theme.css
```

`extract_theme.py` needs the vault to contain the ITS Theme and the Cards
snippet. The example vault has no `.obsidian/` directory, so the site builds
unstyled from it — the example proves the parser, not the theme extractor.

## Layout

```text
scripts/
  vault_parser/   read the vault, transform it, emit content collections
  extract_theme.py, build_fonts.py
src/
  layouts/ components/ pages/   the Astro site
  styles/                       see below
examples/example-vault/         a four-note vault to run against
vendor/fonts/                   Inter sources, subset by build_fonts.py
```

`scripts/README.md` documents the pipeline in detail: the module system, the
publish gate, image provenance, the theme extractor, and how to check that a
CSS change has not altered the site's appearance.

### Stylesheets

Order matters, and `src/layouts/BaseLayout.astro` is where it is declared.

| file | role |
|---|---|
| `fonts.css` | generated `@font-face` rules; must precede the theme |
| `base.css` | variable defaults and element bindings the theme is written against — loads first so the theme overrides it |
| `vault-theme.css` | generated from the vault's theme and snippets (not tracked) |
| `site-overrides.css` | everything that has to beat the theme — web adaptations and the contrast deviations; loads last so it wins |

## Licensing

Copyright is held by the **Nia Tero Worldsmiths** — Marioespiro, Emperadoracero
and Rei-pyo.

Everything in this repository is **MIT** (see `LICENSE`), with two third-party
exceptions noted in `THIRD-PARTY.md`: the Inter sources under `vendor/fonts/`
are SIL OFL 1.1, and the navigation icons inlined in `NavTree.astro` are CC BY
4.0 from Font Awesome Free.

The *content* a vault produces is licensed separately and is not distributed
here. For the Nia Tero site: text under CC BY-SA 4.0, images not licensed at
all. `LICENSE-CONTENT` states those terms and the site's own footer carries
them.
