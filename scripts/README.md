# vault_parser

Parses the Obsidian vault into Astro content collections.

**The vault is read-only.** Nothing in this project may write to it. `vault.py`
is the only read path and hardcodes read-only modes; `emit.py` is the only
write path and refuses to write anywhere except the three configured output
directories.

## Pointing at a vault

`vault.config.yaml` is not committed -- it names a path particular to one
machine. Start from the copy that is:

```bash
cp vault.config.example.yaml vault.config.yaml
```

`vault_root` is the single thing tying the parser to one particular vault. It
accepts an absolute path or one relative to the config file, and symlinks are
resolved before use, so all of these work:

```yaml
vault_root: C:/Users/you/Vaults/My Vault    # absolute
vault_root: ../some-vault                   # relative to the config
vault_root: src/vault                       # a symlink inside the project
```

Nothing else in the pipeline hardcodes a path, so pointing this elsewhere runs
the whole thing against a different vault. What *is* vault-specific is
configuration rather than code: `collections` (top-level folder to Astro
collection), `ignore_dirs`, and the theme extractor's expectation of an ITS
Theme at `.obsidian/themes/`.

## Usage

Preview a run without writing anything:

```bash
.venv/Scripts/python -m scripts.vault_parser --dry-run --verbose
```

Write the output:

```bash
.venv/Scripts/python -m scripts.vault_parser
```

Every run prunes stale output, so the result is a pure function of the vault.
`--clean` wipes the output directories first and is only needed after changing
the output layout itself.

| Flag | Effect |
|---|---|
| `--dry-run` / `-n` | report only, write nothing |
| `--clean` | remove previous output first |
| `--verbose` / `-v` | full problem lists instead of the first 10 |
| `--only NAME` | run only these modules, ignoring config (repeatable) |
| `--also NAME` | enable these modules *alongside* the configured set (repeatable) |
| `--skip NAME` | disable these modules for this run (repeatable) |
| `--list-modules` | show every registered module and its state |
| `--config PATH` | use a different `vault.config.yaml` |

Exit codes: `0` ok, `2` bad config or arguments, `3` a safety guard fired
(always a bug -- never work around it).

## Setup

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r scripts/requirements.txt
```

## Deploying from CI

`.github/workflows/deploy.yml` runs the whole pipeline on a GitHub runner and
uploads the result to Cloudflare Pages. It is manual (`workflow_dispatch`):
with `default_publish: false` most vault edits touch unpublished notes and
cannot change the site, so building on every push would mostly reproduce
identical bytes.

**The workflow names no vault, no site and no domain.** Those live in
repository variables, which is what keeps this repository the generic tool:

| Variable | Purpose |
|---|---|
| `VAULT_REPO` | `owner/repo` of the vault to build |
| `VAULT_REF` | vault branch/tag/SHA (default `main`) |
| `CF_PROJECT` | Cloudflare Pages project name |
| `CF_PRODUCTION_BRANCH` | must match the project's production branch (default `main`) |
| `MIN_NOTES` | floor for the empty-parse guard (default `40`) |

Secrets: `VAULT_TOKEN` (contents-read on the vault repo), `CLOUDFLARE_API_TOKEN`
(Pages: Edit) and `CLOUDFLARE_ACCOUNT_ID`.

`vault.config.ci.yaml` is the config it runs with -- a copy of
`vault.config.example.yaml` differing only in `vault_root: .vault`, the
directory the workflow checks the vault out into. **Keep the two in step.**
`.vault` is deliberately outside all three output directories, so the "no
output inside the vault" guard cannot fire, and dot-prefixed so Astro ignores it.

Two guards matter more than they look:

- **Empty-parse guard.** The parser exits `0` with only a stderr warning when
  nothing is emitted, and Astro's glob loader merely *warns* on a missing
  collection directory. Without a floor on the note count, a misconfiguration
  deploys a live, empty site instead of failing.
- **Fingerprint guard.** A hash over every path and its contents in `dist/`,
  used as an Actions cache key; a hit means this exact build already shipped, so
  the upload is skipped. It only works because the build is **reproducible** --
  see below.

### Reproducible builds

Two clean builds of an unchanged vault must produce byte-identical `dist/`.
That is what lets CI tell "nothing changed" from "something changed", and it is
easy to break: anything per-render and non-deterministic (a `Math.random()` id,
an embedded timestamp) makes every page differ on every build. `GraphView.astro`
derives its instance id from the mode for exactly this reason.

Check it after touching any component:

```bash
npx astro build && (cd dist && find . -type f -print0 | sort -z \
  | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
```

Run it twice; the two hashes must match. The same one-liner compares a local
build against CI's, which prints its fingerprint to the job summary.

## Output

| Path | Contents |
|---|---|
| `src/content/<collection>/<slug>.md` | parsed notes, validated by `src/content.config.ts` |
| `src/generated/*.json` | sidecar artifacts from `finalize()` modules |
| `public/vault-assets/` | referenced images |

All three are generated but **committed** -- they are what the site builds
from, and the vault itself is never committed to this repo.

## Adding a module

Each iteration of this project is one module. The core does not change.

1. Write `modules/mNN_<name>.py` subclassing `TransformModule`. Set `name`,
   `order`, `summary`, and `stub = False`. Override `transform(note, ctx)` for
   per-note edits, or `finalize(ctx)` to emit a sitewide artifact into
   `ctx.generated`.
2. Add the class to `_MODULE_CLASSES` in `modules/registry.py`.
3. Add `<name>: { enabled: true }` under `modules:` in `vault.config.yaml`.

`ctx` is the global index built in pass 1 -- `ctx.find_note()` implements
Obsidian's resolution order (path, then title, then slug, then alias) and
`ctx.find_asset()` does case-insensitive image lookup. Use them rather than
re-walking the vault.

Current order: `10 comments -> 20 links -> 30 inline_dataview -> 40 callouts
-> 50 cleanup -> 60 dataview_queries -> 70 nav_tree -> 80 zoommap
-> 82 image_licensing -> 85 empty_headings -> 90 graph`.

**Order is load-bearing.** Obsidian strips `%%comments%%` before it parses
block structure, so `comments` must stay first: a commented line inside a
callout does not end that callout in Obsidian, and a commented-out wikilink
must never become a real link. Getting this wrong silently split an infobox on
a published page into an orphaned blockquote.

`empty_headings` is the other order-sensitive one: it removes headings with
nothing under them, and "nothing" is only knowable after every module that adds
content has run -- a heading followed by a ```dataview fence is not empty once
Module 6 executes it, and one followed only by a `%%comment%%` or a `#WIP` tag
becomes empty only after Modules 1b and 5. Hence 85, not 50.

Its rule is recursive: a heading is empty when it has no own content *and*
every heading nested beneath it is empty too, so a parent inherits substance
from its children. That covers mid-note sections, not just the tail.

`zoommap` reads three files per map: the ```zoommap fence in the note, the
sibling `*.markers.json` (pins in normalised 0-1 coordinates), and the plugin's
`.obsidian/plugins/zoom-map/data.json` for icons, which are self-contained
data-URI SVGs and so need no copying. The rendered world is sized in the
image's **natural pixels**, because that is what the plugin's zoom levels mean:
`maxZoom: 1` is 100% of the source, not "fit the container". Sizing to the
container instead would make Tambler's `maxZoom: 1` un-magnifiable.

`icons.py` gives an `imagen:` frontmatter value the same treatment when it
names a file under `asset_exclude_dirs` (the vendored Font Awesome / RPG
Awesome packs) rather than a real vault asset -- inlined as a data URI,
never copied. `linking.resolve_image()` is the shared lookup: a real asset
first, an icon second, `None` if neither matches. Modules 2, 3 and 6 all
route through it, so `imagen: castle-flag.svg` resolves the same way whether
it appears as a body embed, an infobox field, or a query portrait. The name
index (a directory listing) is built once per run on first use; a file's
bytes are only read for names actually referenced, since the vault carries
~2500 of these and none but the referenced ones are content.

`graph` emits `src/generated/graph.json` -- nodes, edges, per-note backlinks,
and appearance copied from the vault's `.obsidian/graph.json` (the five
path-based colour groups, `showOrphans`, `showArrow`, and the force
parameters), so the site's graph resembles Obsidian's rather than a generic
diagram. It recomputes edges from `raw_body` and frontmatter rather than
reading the transformed text, which by then has links rewritten to `<a href>`
and map fences replaced wholesale; that also makes `--only graph` produce the
same output as a full run.

All modules are implemented. Enabling a stub
is a no-op, reported as `STUB` in the run summary. `empty_headings` is
implemented but **off by default** -- whether an empty heading is noise or a
deliberate placeholder is an editorial call. Preview what it would do without
writing anything:

```bash
.venv/Scripts/python -m scripts.vault_parser --dry-run --verbose --also empty_headings
```

Use `--also`, not `--only`, to preview an off-by-default module. `--only` runs
it alone, and the late modules read markup the earlier ones produce -- on its
own, `image_licensing` sees `![[...]]` instead of `<img>` and reports nothing,
while `empty_headings` judges a document where the dataview fences were never
executed.

`image_licensing` enforces the vault's own `z_Assets/CREDITS.yaml`: each image's
`origin` is looked up in that file's `policy` table, and anything not marked
`publish` -- including any file with no row, since the file defaults to
`unknown` and `unknown` is `never` -- is replaced by a placeholder and kept out
of `public/`. It also writes `src/generated/credits.json` listing the
attributions the surviving images oblige.

It rewrites finished `<img>` markup rather than gating each producer. Four
modules emit images (2, 3, 6, 8) and each builds its tag differently; filtering
in four places is four chances to miss one, and a miss publishes an image the
vault said to withhold. Hence order 82 -- after every producer, and before
`empty_headings` so a section whose only content is a withheld image is not
deleted along with it.

Its `lenient` option publishes a non-publishable image anyway instead of
withholding it, and only reports what enforcement would have done -- for
auditing CREDITS.yaml coverage against the current vault without changing what
the live site serves while that classification work is still in progress.

`nav_tree` applies two shape simplifications after building the faithful tree,
both on by default and switchable per-option: `flatten_leaf_folder_notes` turns
a folder whose only content is its own folder note into a plain note (common
where a folder's real contents are git-ignored `_*` spoiler directories), and
`collapse_chains` welds runs of pure connector folders into one row, so
`Pandysia / Juutei / Regiones / Ryuutei / Ryuujou / Personas importantes` is a
single click. Collapsing skips folders that have their own note, and skips
collection roots so `World Atlas` stays a distinct entry point.

`nav_tree` writes `src/generated/nav-tree.json`, which `BaseLayout` renders on
every page via `src/components/NavTree.astro`. The layout loads it with
`import.meta.glob` rather than a direct import, so disabling the module (which
prunes the file) does not break the build.

Emitted DOM deliberately mirrors Obsidian's, so the vendored theme and the
`Cards.css` snippet apply unchanged: `a.internal-link` / `.is-unpublished` /
`.is-unresolved`, `.callout[data-callout=...]` with `.callout-title` and
`.callout-content`, and `table.dataview.table-view-table` /
`ul.dataview.list-view-ul`.

Query results follow a split publish policy: **names** obey the publish gate
(published notes link, others render as plain text), while **portraits always
render**, because images carry no frontmatter and so have no gate of their own.

Shared helpers worth reusing rather than reimplementing:

- `mdtext.map_prose(text, fn)` -- rewrite prose only, never inside code.
- `mdtext.map_inline_code(text, fn)` -- the opposite: rewrite inline code
  spans, still skipping fences. Inline dataview lives in inline code, so
  Modules 2 and 3 need opposite halves of the same split.
- `mdtext.map_outside_fences(text, fn)` -- weaker protection, for comment
  stripping: `%%` wins over inline code in Obsidian, so a comment wrapping an
  inline-code span must be removable as one unit.

Block-level constructs (callouts) scan lines directly rather than using
`mdtext`, because chunk splitting would cut a block in half.

When emitting a wrapper `<div>` around markdown, surround the content with
blank lines. A `<div>` starts an HTML block that swallows everything up to the
next blank line, so without them the infobox tables would never parse.
- `linking.WIKILINK` -- the one wikilink regex. Do not write another.
- `linking.resolve(ref, ctx)` / `linking.render(ref, resolution)` -- the single
  definition of what a wikilink points at and how it looks.
- `linking.render_text_with_links(text, ctx)` -- for a string mixing prose and
  links; escapes the prose and renders the links in one pass.

Module 2 stores each resolution on `WikiRef.resolution`, which Module 6 should
read rather than resolving again.

Note that Module 2 deliberately leaves frontmatter wikilinks as authored:
Module 6's queries compare against link targets (`where ubicacion =
this.file.link`), which rewriting would break.

## Theme extraction

`extract_theme.py` is a separate deterministic script, not a pipeline module --
the CSS changes far less often than the content does.

```bash
.venv/Scripts/python scripts/extract_theme.py
```

It reads two sources and writes one file, `src/styles/vault-theme.css`, in
cascade order:

1. The vault's ITS Theme, filtered to light mode, the `wotc-beyond` Style
   Settings variant, and the `infobox` callout type.
2. `Cards.css` (filtered) and `custom.css` (verbatim).

Roughly 890 KB of input becomes ~177 KB, of which 41 KB is the `its` icon font.

**The theme sets values; it does not apply them.** `color: var(--link-color)`
appears zero times in its 846 KB. It defines `--h1-size` and never binds it to
`h1`. That binding layer is [`src/styles/base.css`](../src/styles/base.css),
hand-authored and loaded **before** this generated file so the theme overrides
it. Read its header before changing either. Without it the site gets a flawless
palette that nothing consumes: body text in Times New Roman, browser-blue links
sitting on top of a perfectly good `--link-color`.

Four things to know before editing:

**Load order encodes which layer wins.** `base.css` must lose to the theme and
`site-overrides.css` must beat it; several selectors appear in both `base.css`
and the theme at identical specificity, so source order alone decides. The
import block in `BaseLayout.astro` is the authority and is commented
accordingly.

**App-shell declarations are stripped from `html`/`body`.** Obsidian is a
fixed-viewport application and pins the document to the window with
`overflow: clip; height: 100%`. Import that into a static page and the site
becomes unscrollable with all its content below the fold. `SHELL_PROPERTIES`
drops those declarations from root-element rules only -- fonts, colours and
variables in the same rules are kept. It is a no-op against the theme today and
is kept as a guard against a future theme update.

**`ALLOWED_CLASSES` is a contract with the emitted HTML.** A module that starts
emitting a new class must have it added there, or the theme rule styling it is
silently dropped. `rescue_variables()` covers the related failure -- a kept rule
referencing a variable whose defining block was filtered out -- by recovering
the definition from source.

It reports what it could *not* recover:

```
WARNING: 5 variable(s) used but undefined:
  --background-modifier-accent, --checkbox-icon, --font-adaptive-small,
  --font-size, --tbl-w
```

Those are used with no fallback and defined nowhere, so the declarations reading
them resolve to nothing and the element quietly inherits instead -- no error,
just a slightly different rendering. The five above are left alone deliberately:
defining them would change how the site looks, and the site currently matches
the vault. Fix them when you next want that change, by declaring them in
`base.css`. Names `base.css` already declares are excluded from both the rescue
and the warning -- it is the authoritative source for those defaults, and
hoisting the theme's value for one of them into `:root` silently outranks it.

**Overrides need the theme's specificity, not just a later load order.** The
theme sets its variables on `.theme-light.wotc-beyond`, so a `:root` override
loses no matter when it loads. The variable block at the top of
`site-overrides.css` matches that selector deliberately.

### Checking the site still looks like the vault

`base.css` was written by measurement, and the same method re-checks it. Build,
serve `dist/`, and record `getComputedStyle` for a fixed list of targets across
a set of pages; do it once before a change and once after, and diff. Compare
colour exactly, lengths with a half-pixel tolerance, and typography by the
*resolved* font face rather than the declared stack -- a `font-family` string
can differ while naming families that do not exist, and resolve identically.
Measure the face with canvas text metrics.

Three traps worth knowing if you rebuild the harness:

- The graph's force simulation settles differently on every page load. Two
  captures of the *same* build differ, so its nodes have to be excluded or they
  produce phantom failures.
- The pan/zoom map sizes itself from script after the image loads. Capture
  before that runs and the map measures ~18% narrow — a convincing-looking
  regression that is really a race. Wait for `.zoommap-world` to have a
  non-identity transform, or just re-capture and see if it moves.
- Eleven pages cover every element the site emits, which a greedy set-cover
  over `dist/**` will find for you.

Measure the built site (`npm run build` then `npm run preview`), not the dev
server: the dev server injects each stylesheet as its own tag, and load order
between them is the thing most likely to break.

### Deliberate deviations from the vault's appearance

Only two, both contrast-related, both in `src/styles/site-overrides.css`. Each
value is the smallest darkening that clears WCAG AA, found by lowering lightness
in HLS while holding hue and saturation, so the colour stays in its family:

| | before | after |
|---|---|---|
| inline links `#df6262` -> `#d63636` | 3.31:1 | 4.52:1 |
| infobox header bg `#cd645e` -> `#c75049` | 3.13:1 | 4.49:1 |

Everything else measures AA already: body text 11.99:1, h3 7.70:1, h1 4.83:1.

Two further notes, both pre-existing and both left alone:

- The infobox `th` fix above reaches only odd columns. Tables alternate
  *columns*, and that rule out-specifies the fix, so an even-numbered header
  cell keeps the theme's lighter tint.
- Five variables are used but undefined (see the extractor's warning above).
  Defining them would change the rendering.

## Fonts

```bash
.venv/Scripts/python scripts/build_fonts.py
```

The site sets its text in **Inter**, which is what the vault renders in: the
theme's `--font-default` lists display faces ahead of it that are not installed
there and are not shipped here, so Inter is the entry the stack actually
resolves to. `site-overrides.css` names it first to make that explicit
instead of incidental, which is also why `fonts.css` must load *before* the
theme -- otherwise `--font-default` resolves to a face that does not exist.

Inter is a *variable* font: one file spans weights 100-900, so upright plus
italic covers everything, **740 KB -> 250 KB**. It is OFL-licensed and the
sources live in `vendor/fonts/`.

To ship a different family, add it to `FAMILIES` in `build_fonts.py`, drop its
sources into `vendor/fonts/`, and select it with `--family`. Output from the
previous family is pruned automatically.

The subset ranges are chosen for *this* vault and are worth checking before
adding content in a new script: Latin-1 (Spanish accents), Latin Extended-A
(Esperanto `ŝ ĝ ĉ ĵ ĥ ŭ`, as in `Saŝa` and `Miĉjo`), General Punctuation, Box
Drawing (`└` in the infobox templates) and Geometric Shapes (`◆`, the
folder-note marker). A character outside them renders as tofu.

Note `recalcTimestamp=False`: fontTools otherwise stamps the build time into
`head.modified`, and every run would emit different bytes.

## Regression checks

Run these after every module. The last two are what protect the whole project:

1. `git -C <vault> status --porcelain` is unchanged -- the read-only guarantee.
2. `npx astro build` passes -- Zod catches frontmatter drift.
3. Re-running the parser produces no `git diff` -- determinism.

Caveat on check 3: `sourceModified` comes from the source file's mtime, so an
edit that is later reverted still changes it. Check 3 is exact for back-to-back
runs; across time, a `sourceModified`-only diff means the file was touched, not
that the parser is non-deterministic. Confirm what changed in the vault before
treating such a diff as a parser bug.
