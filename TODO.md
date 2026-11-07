# Pending work

Things deferred on purpose, and things found along the way that nobody has got
to yet. Started 2026-11-07 from a sweep of every open thread.

This file spans all three places the project lives — this repository, the
plugin repository (`../obsidian-i18n-manager`) and the vault — because several
items only make sense across them. Each entry says what it is, why it matters,
and what closing it would take, so an item can be picked up cold.

Close an item by deleting it. If something turns out not to be a problem, say
so in one line before deleting, the way the notes below do.

---

## Content and rights

### Three published images have no permission to be published

`z_Assets/CREDITS.yaml` calls this "the one job with a deadline", and it is the
only item here with a legal edge rather than a technical one.

| File | Origin recorded | Status |
|---|---|---|
| `Micĵo.webp` | `unknown` | published |
| `Gary.jpg` | `external` | published |
| `Pom.webp` | `external` | published |
| `Ramiel.webp` | `unknown` | not published |

`origin: unknown` resolves to `policy.unknown: never`, so the parser already
withholds all four from a built site and substitutes the stand-in portrait —
the site is not currently distributing them. The problem is the vault, where
they are in use, and `THIRD-PARTY.md`, which cannot state a licence for the
images while their origin is unsettled.

**What it takes:** find out who made each one, and record it. If an origin
cannot be established, the honest outcome is to replace the image rather than
leave it at `unknown` indefinitely.

### `zora.png`, 2.3 MB, still a PNG

The largest published asset that is not a map. It stayed PNG because `Coralia`
is in the normaliser's exclusion list, which was the right call at the time —
that folder was being worked on.

**What it takes:** take `Coralia` out of **Settings → i18n Manager → folders to
leave alone**, run the normalise pass, and fix the references it reports as
stranded. The run tells you how many there are before it does anything.

---

## This repository

### Astro is four advisories behind

Installed 7.1.6. `npm audit` reports one critical and three lesser issues, all
in Astro itself:

| Severity | Issue | Fixed in |
|---|---|---|
| critical | RCE through AVIF image optimization | 7.2.8 |
| moderate | Authorization bypass when stripping the configured `base` | 7.2.4 |
| moderate | Reflected/stored XSS via unescaped `<script>`/`<style>` in JSX/MDX | 7.3.2 |
| low | CSS injection in View Transitions | > 7.3.2 |

None looks reachable here: the site is a static build with no `base`, no
MDX and no View Transitions, and it never calls `astro:assets` — the images go
through the parser and are served as plain files, so no AVIF optimisation
endpoint exists to attack. That is an argument for not panicking, not for
staying on 7.1.6.

**What it takes:** `npm install astro@^7.3.3`, rebuild, and check the 49 pages
still render. A minor-version bump within Astro 7 should be uneventful.

### The repository has no `.gitattributes`, and Windows tooling exploits that

`core.autocrlf` is `false` and nothing declares a text policy, so whatever line
endings a tool writes are what git stores. Python's `Path.write_text()` without
`newline=""` translates `\n` to `\r\n` on Windows — which is how a six-file,
442-line change in the plugin repository briefly presented itself as a
5,000-line rewrite of every file it touched. Caught before committing, but only
because the diff looked absurd.

The damage is not cosmetic: a whole-file rewrite destroys `git blame` and hides
the real change inside it.

**What it takes:** add `* text=auto eol=lf` as `.gitattributes` in both
repositories, then `git add --renormalize .` once. Worth checking what that
renormalise touches before committing it — it is a repository-wide change and
should be its own commit, not a passenger on someone else's.

### The Japanese font is 2 MB in one file

`public/fonts/noto-sans-jp-variable.woff2` holds kana, CJK punctuation and the
6,355 kanji of JIS X 0208. It is only fetched when Japanese actually renders,
so a reader of Spanish never pays for it — but a reader of Japanese pays all of
it at once, on the first page.

This is fine at the vault's current volume of Japanese. If it stops being fine,
the escalation is what Google Fonts does: slice the kanji across many
`unicode-range` blocks so a page fetches only the slices it touches. That is
roughly 40 more files and 40 more `@font-face` rules, which is why it is not
where we started.

The cheaper half-measure is to drop to JIS X 0208 **level 1** — 2,965 kanji,
1.1 MB, a one-word change in `jis_x0208_kanji()`. It covers every kanji in the
vault today but misses name kanji such as 翔, 煌, 颯 and 凜, so it trades a
megabyte for the risk of a character's own name rendering in a different font
from the sentence around it.

### `cols:` for card columns in `i18n-query`

Deferred by decision when the query component was built: a per-section `cols:`
key letting a `groups:` entry lay its cards out in a fixed number of columns
rather than reflowing. Never started — there is no trace of it in `scripts/`,
`src/styles/` or `docs/`.

**What it takes:** it is layout data, so it belongs in the shared table beside
`columns`, `sort` and `sections`, and needs to land in both readers — the
parser's `grouping.py` and the plugin's `src/query/` — or Obsidian and the site
will disagree.

### Chapter lists in `tomo` notes, through `i18n-query`

Scoped and closed as "no code change needed": both the parser and the plugin
already handle it, and both test suites already contain tests named for this
case. What is missing is vault-side.

Ordering should come from a numeric frontmatter key, not from the
`anterior`/`siguiente` chain. The chain's integrity depends on 2n links and it
is already broken — `El ojo de la tormenta` has two heads, two tails, and a gap
where `2 - El camino a la capital` has no `siguiente`, so a forward walk
silently drops `3 - Preparativos de guerra`. A broken chain is indistinguishable
from a finished book. A numeric key depends on n values and a missing one is
visible rather than truncating.

**What it takes (vault side, yours):** a `capitulo:` number in each chapter's
frontmatter, and a `tomo:` entry in the shared table with
`sort: { by: capitulo, numeric: true }`. Both readers use dataview's own
unanchored number regex and both sort nulls last, so Obsidian and the site will
agree without any further work.

### Two small things nobody has traced

- The parser reports **7 portrait fallbacks** against **6 withheld images**,
  while 10 pages carry the stand-in and 15 references to it exist in total.
  Three different quantities are being counted — substitutions, distinct
  withheld images, and all uses — so it is probably right, but nobody has
  followed it through.
- Slug collision `caimos → caimos-ubicaciones`, reported on every run. The
  tiebreak is deterministic because the walk is sorted, and neither note is
  published, so today it is noise in the report and nothing more.

---

## Plugin repository

### `minAppVersion` understates the real floor by two minor versions

`manifest.json` says `1.4.0`. The actual floor, read from the `@since` tags in
the installed typings (`obsidian` 1.13.1):

| API | Used in | Added in |
|---|---|---|
| `AbstractInputSuggest` | `src/settings.ts` — the folder picker | **1.4.10** |
| `Vault.getAllFolders()` | `src/settings.ts` — populating it | **1.6.6** |
| `FileManager.trashFile()` | `src/normalize/run.ts` | **1.6.6** |
| `Vault.getFileByPath()` | several | 1.5.7 |

So the floor is **1.6.6**. Everything else the plugin touches predates 1.4.0.

The failure is not graceful. `settings.ts` evaluates
`class FolderSuggest extends AbstractInputSuggest<TFolder>` at module scope, and
`extends undefined` throws while the bundle is being evaluated — so on an
Obsidian between 1.4.0 and 1.4.9 the *entire plugin* fails to load, not just the
folder picker. Between 1.4.10 and 1.6.5 it loads and then fails at runtime the
first time the settings tab or the normalise pass reaches for the two 1.6.6
methods.

Nobody is affected today: this machine runs Obsidian 1.15.0, and the plugin is
installed by building straight into `.obsidian/plugins/` rather than through
the community store, so no version negotiation happens at all.

**What it takes:** set `minAppVersion` to `1.6.6` in `manifest.json` and
`{"0.1.0": "1.6.6"}` in `versions.json`. That is a two-line change and it only
writes down what is already true — it does not drop support for anything that
currently works.

The alternative, if supporting older Obsidian ever matters, is to feature-detect
instead: guard the folder picker behind a check for `AbstractInputSuggest` and
fall back to a plain text field, and replace `getAllFolders()` and
`trashFile()` with their older equivalents. That is real work for a benefit
nobody here needs, which is why bumping the number is the recommendation.
