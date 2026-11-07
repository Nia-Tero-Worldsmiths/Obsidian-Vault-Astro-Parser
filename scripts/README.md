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

All three are generated and **git-ignored** -- they depend on a particular
vault, and this repository holds the tool rather than any vault's output. A
fresh clone therefore has nothing to build until `vault_root` points somewhere;
CI regenerates all three on every run.

## Adding a module

Each iteration of this project is one module. The core does not change.

1. Write `modules/<name>.py` subclassing `TransformModule`. Set `name`,
   `order`, `summary`, and `stub = False`. The file is named for what the
   module does; `order` alone sequences it, so there is no number to keep in
   step and no renaming when one is inserted between two others. Override `transform(note, ctx)` for
   per-note edits, or `finalize(ctx)` to emit a sitewide artifact into
   `ctx.generated`.
2. Add the class to `_MODULE_CLASSES` in `modules/registry.py`.
3. Add `<name>: { enabled: true }` under `modules:` in `vault.config.yaml`.

`ctx` is the global index built in pass 1 -- `ctx.find_note()` implements
Obsidian's resolution order (path, then title, then slug, then alias) and
`ctx.find_asset()` does case-insensitive image lookup. Use them rather than
re-walking the vault.

Current order: `10 comments -> 15 lang_blocks -> 20 links
-> 30 inline_dataview -> 40 callouts -> 50 cleanup -> 60 dataview_queries
-> 65 i18n_infobox -> 70 nav_tree -> 80 zoommap -> 82 image_licensing
-> 85 empty_headings -> 90 graph`.

**Order is load-bearing.** Obsidian strips `%%comments%%` before it parses
block structure, so `comments` must stay first: a commented line inside a
callout does not end that callout in Obsidian, and a commented-out wikilink
must never become a real link. Getting this wrong silently split an infobox on
a published page into an orphaned blockquote.

`empty_headings` is the other order-sensitive one: it removes headings with
nothing under them, and "nothing" is only knowable after every module that adds
content has run -- a heading followed by a ```dataview fence is not empty once
`dataview_queries` executes it, and one followed only by a `%%comment%%` or a `#WIP` tag
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

`links` stores each resolution on `WikiRef.resolution`, which `dataview_queries` should
read rather than resolving again.

Note that `links` deliberately leaves frontmatter wikilinks as authored:
`dataview_queries`'s queries compare against link targets (`where ubicacion =
this.file.link`), which rewriting would break.

## Languages

The vault is moving to the [i18n Manager](https://github.com/Nia-Tero-Worldsmiths/obsidian-i18n-manager)
plugin's format: body prose wrapped in `:::lang <codes>` blocks, and infoboxes
declared as a bare ```` ```i18n-infobox ```` fence that the plugin renders from
the note's `NoteType` plus a shared table in the vault. Two modules handle it.

`lang_blocks` (15) splits a note into one finished body per language and stores
them on `note.lang_bodies`; `Pipeline.run_note` then runs every *other* module
once per body. That is the load-bearing decision in the whole feature. The
plugin can afford to hide other languages with a CSS class because Obsidian
re-renders live, but several modules here reason about document structure --
`empty_headings` decides whether a heading is empty from what follows it, and
its rule is recursive. Handed a document holding three languages at once it
would delete a Spanish parent heading whose only substantive child was
Japanese. Splitting first means those modules never learn i18n exists.

It also means `:::lang` never reaches the emitted markdown, which is what keeps
the door open to per-language routes (`/ja/<slug>`) later: that would change
`emit` and `[...slug].astro` and nothing else.

The visibility rule is the plugin's, and worth stating exactly because it is
easy to misread. The threshold is a *single line number*, `blocks[0].openLine`:

- above the first opening marker -- shared, every language sees it;
- inside a block -- that block's languages, or all of them for `:::lang all`;
- at or after the first opening marker but outside every block -- the default
  language only. **This catches text between two blocks**, not just text after
  the last one. The dry-run report lists notes where that happens, because it
  is the easiest mistake to make while converting a note.

`i18n_infobox` (65) renders the fence. Its resolution logic is a port of the
plugin's `src/infobox/model.ts`, kept in `infobox.py` free of pipeline
machinery so it stays testable on its own -- that file and `langblocks.py` are
what `tests/` covers, and those tests are ports of the plugin's own. When the
plugin's suite gains a case, port it here too; this is a second implementation
of rules that exist in exactly one other place.

Both infobox formats render at once, so the vault can convert a few notes at a
time. The hand-written `>[!infobox]` callout is `callouts` + `inline_dataview`;
the fence is a separate construct and the two never touch. The report always
prints how many published notes are still on the old one, and
`warn_legacy: true` lists them.

### Several images in one infobox

A note may name more than one picture, each with a label, and the site draws a
switcher:

```yaml
imagen:
  - Aronne Laqua.webp
  - Aronne y Donatello.webp
imageLabel:
  - Forma verdadera
  - Forma humana (con Donatello)
```

The two are parallel lists paired by position, the same way `clase` and
`subclase` are. That shape was chosen over a combined `file :: label` string
for one reason above all: a label has to be translatable on its own.
`imageLabel_ja: [肖像, 鎧]` overrides only the words, where a combined string
would force every filename to be restated in every language, free to drift.

Translation needs no new code -- labels go through `resolve_positional_values`
like any other value, so `imageLabel_ja` works per note and, more usefully,
`values.imageLabel` in the table translates a label once for the whole vault.
*Retrato* and *Armadura* repeat across notes, which is what that table is for.

Two rules decide what gets drawn, and they are deliberately separate:

- **Alt text** follows from a label existing. Wherever one is given it
  describes that image, with or without a switcher. It replaces using the note
  title for every picture, which told a screen-reader user nothing the heading
  above had not already said.
- **Buttons** follow from there being a choice. One image renders exactly the
  markup it always did, so a note that merely labels its single portrait does
  not change shape.

The chooser is a native `<select>`, not a row of buttons. The infobox column
is about 290px wide and these labels are sentences -- "Forma humana (con
Donatello)" is 182px of it on its own. Two fit across, barely; five would not,
and a row that scrolls shows a reader one option at a time, which is the
opposite of what a chooser is for. Collapsed, a dropdown is one line whatever
the count, and being native it gets keyboard, screen-reader and mobile-picker
behaviour for free.

Ids come from the slug and language (`{slug}-{lang}-img`) rather than a
counter -- a per-render number would differ between builds and break the
`dist/` fingerprint CI uses. `src/components/ImageGallery.astro` adds
`is-interactive` and wires the select; until it does, every panel is visible
with its label as a caption, so with no JavaScript a gallery is a captioned
list and no image is unreachable.

### Clicking a picture to see it full size

`src/components/Lightbox.astro` claims every image inside `<main>` and opens
it in a `<dialog>`. It renders no markup of its own and the parser knows
nothing about it: six modules emit `<img>` tags and a seventh would be covered
without anyone remembering to opt in.

**Maps are excluded**, because a `zoommap` already answers the question — it
pans and zooms in place with its own pin layer, and a flat picture of it would
be a downgrade. The withheld-image glyph is excluded too: it is a statement
that a picture is missing, not a picture. The stand-in portrait is *not*
excluded, because it is a real drawing.

Eligibility is decided once, in the component, and marked with `.is-zoomable`
for the stylesheet to key off. A CSS rule that tried to restate the exclusions
would be a second definition free to drift from the first.

The scroll lock is `body:has(dialog.lightbox[open])`, not a class the script
adds and removes. Escape is the one route out the script does not control, and
a lock released by a handler is a lock that can be left on — a reader stranded
on a page that will not scroll, with nothing on screen to explain why. Written
this way it holds exactly while the dialog is open, and on a browser without
`:has()` it simply does not apply, which is the harmless failure.

Images get `tabindex` and `role="button"` so the modal is reachable without a
mouse. That is a real cost on `/galeria`, where it adds 43 tab stops, and the
alternative was a feature only mouse users could reach.

### Which image stands for a note

`defaultImageIndex` is zero-based and answers one question asked in two
places: the image the infobox opens on, and the image shown where the note is
referenced -- a roster portrait, a legacy infobox embed. Absent means the
first, so the ~99 single-image notes never write one.

```yaml
imagen: [Aronne Laqua.webp, Aronne y Donatello.webp]
defaultImageIndex: 1
```

An index out of range is reported and falls back to the first image, rather
than being clamped silently: the key exists to be deliberate, so one left
behind after images were removed should be seen. What it cannot catch is the
subtler case -- reorder `imagen` and a still-valid index quietly points at a
different picture. That is the standing cost of addressing images by position
instead of by name, and worth remembering when rearranging a list.

`imagen` stays a plain string on the ~100 notes that have one picture. Both
shapes are read everywhere -- `infobox.first_image_ref` is the single answer
to "which image does a one-picture context use" -- so notes convert one at a
time and nothing has to be migrated in a batch.

### NoteTypes that carry no infobox

A `NoteType` has three possible standings in the table, not two:

```yaml
layouts:
  persona: { image: imagen, sections: [...] }   # renders
  prosa:   { infobox: false }                   # deliberately none
  # absent                                      # nobody defined it yet
```

Only the third is an error. A type declared `infobox: false` drops the fence
and says so in the run report; a type missing from the table draws a warning
box on the page, because that is a gap someone still has to fill. Keeping the
two apart means a prose chapter is not nagged for being prose, while a
half-finished NoteType still is.

It lives in the table rather than in parser config because the plugin reads
the same file, so Obsidian and the site can agree about it.

### Which languages the site serves

Two lists, deliberately not one:

```yaml
i18n:
  languages: [es, ja, en]   # what the vault and the plugin can author in
  default: es
  ignore: [en]              # supported, but not served yet
```

`languages` mirrors the plugin's own setting. `ignore` is the *site's*
publishing decision, and subtracting it gives `served`, which is what actually
gets emitted.

Collapsing them into one list looks tempting -- deleting `en` from `languages`
would also stop English being emitted -- but it loses a distinction worth
keeping. With `en` declared and ignored, a `:::lang en` block is a deliberate
draft and is reported as content the site chose not to serve. With `en` simply
absent, that same block is indistinguishable from a typo like `:::lang eng`,
and both come out as "unknown language code".

It also decides what `:::lang all` expands to, which is the practical reason it
exists: without it, a note whose only shared content sits in an `all` block
sprouts a pill for a language that has nothing but that shared content behind
it.

Two guards, both hard config errors: the default language cannot be ignored
(every note falls back to it, so the site would come out empty), and a code
that is not in `languages` cannot be ignored (almost always a typo, and doing
nothing silently would hide it).

**Do not express this by editing the plugin instead.** Its language list lives
in `.obsidian/plugins/i18n-manager/data.json`, which the vault git-ignores, so
the change would reach neither your co-authors nor CI -- and making it stick
would mean editing `DEFAULT_SETTINGS` in the plugin's source to record a
publishing decision. It would also take away the ability to *draft* in that
language, and leave `i18n-infobox.yaml` promising a language the plugin no
longer offers.

### Two YAML shapes PyYAML will not read

Obsidian's YAML parser accepts both of these and PyYAML rejects the whole
document over either, so `frontmatter.normalize_flow_sequences` rewrites them
before parsing -- the same move `_sanitize` already makes for bare wikilinks:

- **`[clase, subclase?]`** -- the layout's optional-row marker. `?` is an
  indicator character in flow context. The real vault table hits this, and
  without the fix *every* infobox in the vault fails, not just that row.
- **`[, Tierra]`** -- a positional hole, which is how the table itself
  documents pairing a subclass to the second of two classes. PyYAML refuses an
  empty item, and since `frontmatter.parse` treats an unparseable block as
  empty, such a note would lose its whole frontmatter, `publish` included, and
  drop off the site without a word.

Holes are then *preserved* through `_clean_value`, because for paired keys the
gap is the signal. Consumers that want plain strings filter `None` themselves.

### Fallback portraits

`fallback_images:` maps a NoteType to a stand-in image for notes with no usable
picture -- either they name none, or the one they name is not publishable under
`z_Assets/CREDITS.yaml`. Both halves are covered: `image_licensing` swaps it in
for a withheld portrait, and `inline_dataview` / `i18n_infobox` use it when the
note names no image at all.

Three rules it keeps. The stand-in is checked against `CREDITS.yaml` like any
other image, so it can never become a hole in the policy. It is added to
`ctx.referenced_assets` when used, or nothing would copy it to `public/` and
the `<img>` would 404. And the original `alt` and the "why" tooltip both
survive, so a reader is never told a placeholder is a likeness.

It does not override an author's choice: a note whose `=embed(...)` line is
commented out keeps no portrait, because there is no embed left to fill.

### Query tables: the `i18n-query` fence

The native replacement for the ```dataview tables. The fence is usually empty,
because the layout comes from the note's own NoteType and the filter term from
its own name:

````markdown
```i18n-query
```
````

```yaml
groups:
  organizacion:
    members: { tag: true, field: organizacion }
    columns:
      - { kind: image, key: imagen, size: "500x500", label: retrato }
      - { kind: link, label: nombre }
    sort: { by: title }
    sections:
      - { label: miembros }
      - { label: secundarios, tag: secundario }
```

`groups:` is optional, unlike `layouts:`. Everything that distinguishes one use
of the fence from another is data here, never code in the module -- which is
why a roster and a chapter list are the same component:

| | roster | chapter list |
|---|---|---|
| `members` | `{tag: true, field: organizacion}` | `{tag: false, field: padre}` |
| `sort` | `{by: title}` | `{by: file.name, numeric: true}` |

**Membership** is "either, or": a note belongs when a tag folds to the term
**or** the layout's `members.field` names the subject. The second half is what
finds a member carrying no tags at all. One term covers both because
`grouping.normalize_term` drops case, accents and separators, reconciling the
vault's three spellings:

    #blackShawarma       [[Black Shawarma]]        -> blackshawarma
    #custodios-de-juutei [[Custodios de Juutei]]   -> custodiosdejuutei

Two fence keys exist for a table whose tag is a *role* rather than the
subject's name: `from:` overrides the term, `not:` names a tag that
disqualifies a row outright.

**Sections** are optional and need no fence syntax. A row lands in the first
section whose `tag` it carries; the section with no tag is the catch-all, and
one is prepended if every declared section names a tag -- otherwise an untagged
row would vanish silently. Each section renders as its own `<table>` with a
translated `<caption>`, because `Cards.css` makes `tbody` a grid and every `tr`
a card, so a header row would draw as one more card that looks like a member.
Leave `sections:` out and the table is a single untitled block.

Three things the queries it replaces could not do: column headers, section
captions and row names all follow the active language; the portrait resolves
through `defaultImageIndex` instead of taking whatever `imagen` holds first;
and a row found only through the membership key is listed at all.

**Placement decides the language**, and nothing forces the table to the end of
a note:

| Where the fence sits | Who sees it |
|---|---|
| above the first `:::lang` marker | every language |
| inside `:::lang all`, anywhere in the note | every language |
| inside `:::lang es` | Spanish only, mid-structure if that is where it belongs |
| below the first marker, outside every block | default language only -- reported |

The last row is the one an author can fall into by accident, so it is named in
the build report. Unlike `i18n-infobox`, an unwrapped `i18n-query` is *not*
forced to be shared: a table may legitimately differ per language, and
overriding the placement would remove the control that placement is for.

The markup comes from `grouping.render_table`, the same function
`dataview_queries` uses, so a note switching over changes what it says and
never how it looks. `dataview_queries` stays enabled: the chapter list on
`tomo` notes keeps using it for now.

### Note galleries: the `i18n-gallery` fence

An infobox shows the pictures that *identify* someone -- one at a time, behind
a dropdown, with `defaultImageIndex` naming the one that stands for the note
everywhere else. That is the wrong shape for the rest of the art. A vault with
five drawings of a character wants to show five drawings, and the ones worth
showing are precisely those the author kept out of the infobox.

So a layout declares a second family of keys:

```yaml
  persona:
    image: imagen        # the infobox: one picture at a time
    gallery: gallery     # the fence: all of them at once
    galleryColumns: 3
```

and the note lists them the same way it lists its infobox images:

```yaml
gallery:
  - 250408 Brunhilda.webp
  - 251001 Brunhilda.webp
galleryLabel:
  - Boceto
  - Ropa de calle
```

```markdown
:::lang all
```i18n-gallery
```
:::
```

**Where to put the fence.** At the end of the note, inside `:::lang all`.
That is the one placement that both puts the gallery last and shows it in
every language: an unwrapped fence below the first `:::lang` marker is
default-language content, so a reader in Japanese would see no pictures at
all. The module reports that case rather than guessing, the same way
`i18n-query` does.

Putting those four lines in `z_Templates/Persona.md` is what makes the
gallery feel automatic: every new character note carries the fence, and a
fence with nothing to show renders nothing, so it costs an empty note
nothing.

The heading is generated, not written in the note. A hand-written
`## Galería` is one more thing to keep per language, and the one above a
`ja` reader's gallery would still be in Spanish. The label key comes from the
layout's `galleryHeading`, defaulting to the family name, so defining
`gallery:` under `labels:` is all a table needs.

**One resolver, two families.** `resolve_images` takes the family name as a
parameter, so `gallery` brings `galleryLabel`, `gallerySize` and
`galleryFallback` with it exactly as `image` brings its own three. Pairing by
position, `imagen_ja`-style per-language overrides, translation through the
shared `values:` vocabulary and `|size` suffixes are therefore not reimplemented
-- they are the same code path, and `tests/test_gallery.py` still proves it for
the infobox.

`galleryFallback` is deliberately *not* declared in the vault's table. A note
with no portrait gets a stand-in because a missing portrait is a gap; a note
with no extra art has nothing missing, and a row of stand-ins would be worse
than an empty section. A fence that resolves to no images renders nothing at
all and is reported instead.

**Not a `groups:` entry**, which is where the query table's second use case
goes. A query selects *other notes* by tag or frontmatter key, and both
implementations skip the host note unconditionally; a gallery's rows are one
note's own images. That is an axis `members:` cannot express.

**Not a table either.** `grouping.render_table` always emits a `<thead>`, its
output is a byte-for-byte contract shared with `dataview_queries`, and a
gallery has no columns to head. It emits `<figure>` elements in a
`.ml-gallery` grid, and `galleryColumns` becomes an inline
`--ml-gallery-columns` -- inline because the count is a number the table may
name freely and a stylesheet cannot enumerate.

The grid sits inside a `.ml-gallery-host` wrapper whose only job is
`clear: both`. The theme floats the infobox (`float: inline-start`, 300px), so
an uncleared block after it rises into the space beside it and is squeezed to
whatever width is left -- which on a note with little prose made the gallery a
narrow strip. `Cards.css` clears `table.dataview tbody` for the same reason.

Cards in a row are the same height, because grid items stretch to their row.
The caption is the last child and the picture takes everything above it with
`flex: 1` and `object-fit: contain`, so captions line up across a row and a
short drawing is centred in its card instead of hanging from the top. A
`max-height` keeps one very tall picture from setting the height of every card
beside it.

The grid's CSS is written twice, in `src/styles/site-overrides.css` here and in
the plugin's `styles.css`, under the same class names. That is the standing
rule: parity between the two readers beats one copy of a declaration.

Two things are reported: an image listed in both `imagen` and `gallery`, which
draws it twice, and a fence that found nothing.

### The gallery page: `gallery.json`

`gallery_index` (order 84) writes `src/generated/gallery.json`, which
`src/pages/galeria.astro` renders as a searchable index of every image the site
may show, with its author and the notes that use it.

It selects **by origin**, from `CREDITS.yaml`, rather than from what the notes
reference:

```yaml
  gallery_index:
    enabled: true
    origins: [original, licensed]
```

That distinction is the reason the module exists. `credits.json`, written by
`image_licensing`, lists what the build *shipped* -- the images a published
note happened to reference -- which is right for an attribution footer and
wrong for a catalogue. Eighteen of this vault's original drawings are credited,
publishable and referenced by nothing, so they were invisible: not on the site,
and not in the list of what the site holds.

`ai` is excluded by default even though the vault's policy publishes it:
publishable and wanted are different questions, and this is a catalogue of the
project's own artwork.

**The load-bearing line is `ctx.referenced_assets.add(...)`.** That set is what
the emitter copies, so an image indexed but not registered becomes a 404 on the
page. `image_licensing` uses the same hook for the stand-ins it substitutes. On
this vault it takes `public/vault-assets/` from 26 files to 44, and 14 MB to
20 MB.

Images are copied byte for byte, at whatever size the vault holds them.
Normalising them is the plugin's job and happens in the vault before the parser
runs; there is deliberately no resizing step here, and adding one would put the
same responsibility in two places, one of which cannot be run from Obsidian.

`usedBy` is built from `assets.collect_asset_references`, the same function the
pipeline uses to decide what to copy, so the catalogue's idea of "used" cannot
drift from the build's. Only published notes count -- an unpublished one is not
a link target. That function lives in `assets.py` rather than `run.py` because a
module importing `run` would be a cycle: `run` imports the module registry.

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

### Japanese

Inter has no Japanese, and `i18n.ignore: [en]` means a built site serves `es`
and `ja`, so **Noto Sans JP** ships beside it. Until it did, the one language
whose typography nobody here controlled was the one a reader was most likely to
see set in an arbitrary face -- Hiragino on a Mac, Yu Gothic on Windows, a
Chinese font or tofu on a Linux box that has no Japanese installed, which
renders some kanji with visibly Chinese glyph shapes.

The split is `unicode-range`, which is also why both families sit in the same
stack rather than one replacing the other. A browser downloads a face only when
it renders a character that face claims, so a reader of Spanish never fetches a
byte of Japanese, and a reader of Japanese gets kanji from Noto and Latin from
Inter inside one sentence.

Three faces come out of it:

| Face | Holds | Size |
|---|---|---|
| `noto-sans-jp-variable.woff2` | kana, CJK punctuation, the 6,355 kanji of JIS X 0208 | 2.0 MB |
| `noto-sans-jp-ui.woff2` | the three characters of the language switcher's label | 1.5 KB |

The kanji repertoire is a choice, and `jis_x0208_kanji()` explains it: the
Unified Ideographs block holds 20,992 characters, JIS X 0208 is the 6,355 that
"written Japanese" has meant since 1978, and Python's `shift_jis` codec maps
precisely that set -- so membership is a test rather than a list to maintain.
Level 1 alone (2,965 kanji) would cost half as much and covers everything
currently in the vault, but it misses name kanji, and a character's own name
set in a different font from the sentence around it is the one failure a reader
cannot miss.

The switcher's label gets its own face because it renders on every multilingual
page whether or not the reader has switched: served from the content face,
three characters in a button would pull 2 MB down for somebody reading Spanish.

If the Japanese file ever becomes a problem, the escalation is what Google
Fonts does -- slice the kanji across many `unicode-range` blocks so a page
fetches only the slices it touches. That is roughly 40 extra files and 40
`@font-face` rules, which is why it is not the starting point.

### Subsets

The Latin ranges are chosen for *this* vault and are worth checking before
adding content in a new script: Latin-1 (Spanish accents), Latin Extended-A
(Esperanto `ŝ ĝ ĉ ĵ ĥ ŭ`, as in `Saŝa` and `Miĉjo`), General Punctuation, Box
Drawing (`└` in the infobox templates) and Geometric Shapes (`◆`, the
folder-note marker). A character outside every shipped range renders as tofu.

For Japanese the declared `unicode-range` is deliberately *wider* than the file
-- it names whole blocks while the file holds a chosen part of them. That is
the correct direction to be wrong in: a kanji the file lacks falls through to
the next family in the stack, per character, so it still renders.

### Adding or changing a family

Add it to `FAMILIES` in `build_fonts.py` with the `SUBSETS` entry it should be
cut to, drop its sources into `vendor/fonts/`, and name it in `SITE` if the
site should ship it. A full run prunes output no longer listed; `--family`
builds a subset of them and skips pruning, so a one-off rebuild of one family
cannot delete another's output.

Three stacks under `wotc-beyond` end at a bare `Inter` -- `--font` (headings,
dataview column headers, statblock titles), `--font-monospace` and
`--font-default` -- and `site-overrides.css` overrides all three so a new
family actually gets reached. Adding a face to `FAMILIES` without touching
those stacks ships a font nothing asks for.

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

## Running the tests

```bash
.venv/Scripts/python -m unittest discover -s tests -t .
```

Stdlib `unittest`, so nothing is added to `requirements.txt`. The two suites
are ports of the plugin's own (`tests/infoboxModel.test.ts` and
`tests/syntaxConsistency.test.ts`), kept close to the originals -- same names,
same fixtures -- so a case added there can be brought across mechanically.

The negative half of `test_langblocks.py` matters more than the positive half:
`%%` is ordinary Obsidian comment syntax used ~254 times in this vault, and the
upstream plugin treated `%% lang es %%` as a marker. If those cases ever start
parsing as blocks, notes lose content silently.
