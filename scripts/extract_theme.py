"""Extract the parts of the Obsidian vault theme this site actually uses.

The vault's ITS Theme is 846 KB of CSS, the large majority of it styling
Obsidian's editor chrome -- panes, tabs, ribbons, CodeMirror, modals -- none of
which exists on a static site. This produces `src/styles/vault-theme.css`
containing only what can apply to the DOM the parser emits.

The theme mostly sets values and leaves it to Obsidian to attach them to
elements: it defines `--h1-size` without ever binding it to `h1`. That binding
layer is `src/styles/base.css`, hand-authored and loaded BEFORE this file, so
the theme overrides it. This script does not read or extract Obsidian's own
stylesheet, which is proprietary; see THIRD-PARTY.md.

Run it the same way as the parser; it is deterministic and reads the vault
read-only:

    .venv/Scripts/python scripts/extract_theme.py

What is kept:

* CSS custom properties from `:root` / `body` / `.theme-light`, including the
  `.theme-light .wotc-beyond` palette -- this is where the actual colours live,
  so keeping it is what makes the output resemble the editor.
* Rules whose selectors reference only classes the parser emits (callouts,
  dataview tables, internal links, nav tree) or plain markdown elements.
* `custom.css` in full, and the parts of `Cards.css` that target dataview
  tables.

What is dropped:

* `.theme-dark` -- the site is light-only by the author's decision.
* Every Style Settings variant other than the two this vault selects
  (`wotc-beyond`, `sizing-readable`): dnd, pathfinder, nord, mini, notion, ...
* Editor-only selectors: `.cm-*`, `.workspace-*`, source view, modals, popovers.

The allowlist below is the contract between this script and the emitted HTML.
Adding a class to a module's output means adding it here too, or the theme rule
that styles it is silently dropped.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_parser import config as config_mod  # noqa: E402
from vault_parser import vault  # noqa: E402

# Classes the parser and the Astro layer actually emit.
ALLOWED_CLASSES = {
    # Obsidian container contract reproduced by BaseLayout
    "theme-light", "wotc-beyond", "sizing-readable",
    "markdown-preview-view", "markdown-rendered", "markdown-body",
    # callouts (`callouts`)
    "callout", "callout-title", "callout-title-inner", "callout-icon",
    "callout-content", "callout-fold", "is-collapsed",
    # links (`links`) and inline tags (`cleanup`)
    "internal-link", "external-link", "is-unpublished", "is-unresolved",
    "is-active", "tag",
    # dataview output (`dataview_queries`)
    "dataview", "table-view-table", "table-view-thead", "table-view-tbody",
    "table-view-th", "table-view-tr-header", "list-view-ul",
    "dataview-result-list-li",
    # Cards snippet, driven by `cssclasses` in vault frontmatter. The whole
    # 1-8 range is allowed even though the vault only uses 2 and 3 today:
    # a note using an unlisted one would silently render as a single column.
    "cards", "list-cards",
    "cards-cols-1", "cards-cols-2", "cards-cols-3", "cards-cols-4",
    "cards-cols-5", "cards-cols-6", "cards-cols-7", "cards-cols-8",
    "cards-cover", "cards-align-bottom", "table-100", "trim-cols", "prosa",
    # navigation tree (`nav_tree` + NavTree.astro)
    "nav-tree", "nav-list", "nav-folder", "nav-folder-title", "nav-folder-name",
    "nav-folder-note", "nav-file", "nav-file-title",
    # page shell
    "app", "sidebar", "site-title", "breadcrumb",
    # generic markdown extras Obsidian emits that remark also produces
    "task-list-item", "footnotes", "heading-collapse-indicator",
}

# Any selector mentioning one of these is editor or app chrome.
BLOCKED_MARKERS = (
    ".cm-", "cm6", "markdown-source-view", "is-live-preview", "workspace",
    "titlebar", "status-bar", "view-header", "mod-root", "canvas", "modal",
    "community-modal", "vertical-tab", "publish-renderer", "graph-view",
    "popover", "suggestion", "tooltip", "prompt", "setting-item", "side-dock",
    "ribbon", "sidedock", "file-explorer", "search-result", "tree-item",
    "outline", "backlink", "tag-pane", "mod-macos", "mod-windows", "is-mobile",
    "is-tablet", "is-phone", "theme-dark", "print", "empty-state", "clickable-icon",
    "checkbox-container", "dropdown", "svg", "inline-title", "embedded-backlinks",
    "metadata-", "frontmatter", "banner", "kanban", "excalidraw", "dice-roller",
    "admonition", "obsidian-", "pdf-", "audio-", "video-", "mermaid",
)

# The only callout type in the vault. The theme ships styling for dozens
# (`aside`, `statblocks`, `timeline`, ...), each with its own icon and colours.
ALLOWED_CALLOUT_TYPES = {"infobox"}

# Embedded fonts to keep. The theme inlines two: `its` (41 KB) is its icon
# font, which callout icons, `--hr-icon-font` and `--link-font` all depend on;
# `Fira Code` (314 KB) is a monospace coding face, and after `inline_dataview` has
# substituted the inline dataview expressions there is almost no code left on
# the site to set in it. Dropping it removes 60% of the output.
KEEP_FONT_FAMILIES = {"its"}

# Style Settings variants this vault does not select. `wotc-beyond` and
# `sizing-readable` are the two it does, per .obsidian/plugins/.../data.json.
BLOCKED_VARIANTS = (
    "dnd", "pathfinder", "pathfinder-remaster", "nord", "mini", "notion",
    "its-d", "drwn", "slrvb-g", "slrvb-b", "slrvb-r", "s-d", "t-d",
    "sizing-compact", "sizing-comfortable", "wide", "readable-width",
)

# Obsidian's user font overrides are normally unset by the app. They cannot be
# left undefined here: the theme builds
# `--font-text: var(--font-text-override), var(--font-default)`, and an
# undefined `var()` makes the whole custom property guaranteed-invalid, so
# `font-family: var(--font-text)` would inherit instead and the typography
# would be lost.
#
# `--font-default` is the theme's own, and under wotc-beyond it names display
# faces this site does not ship before falling back to Inter.
# `site-overrides.css` overrides it to name the subset Inter that
# `build_fonts.py` produces, so the stack resolves on its first entry.
FONT_OVERRIDES = """
:root {
  --font-text-override: var(--font-default);
  --font-interface-override: var(--font-default);
  --font-monospace-default-override: ui-monospace, SFMono-Regular, Menlo,
    Consolas, "Liberation Mono", monospace;
}
"""

# Obsidian is a fixed-viewport application: it pins `html`/`body` to the window
# and clips overflow, because scrolling happens inside panes. A static page
# scrolls the document itself, so importing those declarations leaves the site
# unscrollable with all its content below a clipped 100vh body. These properties
# are dropped from root-element rules; everything else in them -- fonts,
# colours, variables -- is kept.
#
# A no-op against the theme as it stands -- the only ITS rules setting these on
# a `body`-rooted selector are descendant selectors, which `_ROOT_ELEMENT`
# excludes. Kept so a theme update that pins the document cannot silently make
# the site unscrollable.
SHELL_PROPERTIES = {
    "overflow", "overflow-x", "overflow-y", "overscroll-behavior",
    "height", "min-height", "max-height", "width", "min-width", "max-width",
    "position", "top", "right", "bottom", "left", "inset", "display",
    "user-select", "-webkit-user-select", "touch-action", "contain",
}

# Only the root element *itself*: `body`, `body.theme-light`, `html`, `:root`.
# Deliberately excludes descendants -- an earlier version allowed anything after
# `body`, which also matched `body:not(...) .callout` and would have stripped
# the infobox's own `width`.
_ROOT_ELEMENT = re.compile(
    r"^\s*(?:html|body|:root)(?:[:.\[][^\s>+~]*)*\s*$", re.IGNORECASE
)

_CLASS_TOKEN = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
_CALLOUT_TYPE = re.compile(r"data-callout[~^*$|]?=\"?'?([\w-]+)")
_FONT_FAMILY = re.compile(r"font-family\s*:\s*[\"']?([^\"';}]+)", re.IGNORECASE)
_CUSTOM_PROPERTY = re.compile(r"^\s*--[\w-]+\s*:", re.MULTILINE)
_ROOTISH = re.compile(
    r"^(:root|html|body|\*|\.theme-light|body\.theme-light"
    r"|\.theme-light\s*\.wotc-beyond|\.theme-light\.wotc-beyond"
    r"|\.wotc-beyond|\.sizing-readable)",
)


@dataclass
class Block:
    """One CSS block: either a style rule or an at-rule with nested content."""

    prelude: str
    body: str
    is_at_rule: bool


def scan(css: str) -> list[Block]:
    """Split CSS into top-level blocks, comment-aware and brace-balanced."""
    blocks: list[Block] = []
    prelude: list[str] = []
    index = 0
    length = len(css)

    while index < length:
        if css.startswith("/*", index):
            end = css.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue

        char = css[index]
        if char == "{":
            depth = 1
            body_start = index + 1
            cursor = body_start
            while cursor < length and depth:
                if css.startswith("/*", cursor):
                    end = css.find("*/", cursor + 2)
                    cursor = length if end == -1 else end + 2
                    continue
                if css[cursor] == "{":
                    depth += 1
                elif css[cursor] == "}":
                    depth -= 1
                cursor += 1

            text = "".join(prelude).strip()
            blocks.append(
                Block(
                    prelude=text,
                    body=css[body_start : cursor - 1],
                    is_at_rule=text.startswith("@"),
                )
            )
            prelude = []
            index = cursor
            continue

        if char == ";" and "".join(prelude).strip().startswith("@"):
            # A statement at-rule such as `@charset` or `@import`.
            prelude = []
            index += 1
            continue

        prelude.append(char)
        index += 1

    return blocks


def selector_allowed(selector: str) -> bool:
    lowered = " ".join(selector.lower().split())
    if not lowered:
        return False

    if any(marker in lowered for marker in BLOCKED_MARKERS):
        return False

    # A rule for a callout type the vault never uses -- the theme ships dozens.
    types = {t.lower() for t in _CALLOUT_TYPE.findall(lowered)}
    if types and not types <= {t.lower() for t in ALLOWED_CALLOUT_TYPES}:
        return False

    classes = {name.lower() for name in _CLASS_TOKEN.findall(lowered)}
    if classes & {v.lower() for v in BLOCKED_VARIANTS}:
        return False

    unknown = classes - {c.lower() for c in ALLOWED_CLASSES}
    return not unknown


def defines_variables(body: str) -> bool:
    return bool(_CUSTOM_PROPERTY.search(body))


def keep_rule(prelude: str, body: str) -> str | None:
    """Return the selectors worth keeping from this rule, or None."""
    selectors = [s.strip() for s in _split_selectors(prelude) if s.strip()]
    if not selectors:
        return None

    kept: list[str] = []
    for selector in selectors:
        lowered = " ".join(selector.lower().split())
        if "theme-dark" in lowered:
            continue

        # Variable-defining root blocks carry the whole palette. Keep them even
        # though `:root` mentions no allowlisted class -- everything else
        # depends on the custom properties they set.
        if defines_variables(body) and _ROOTISH.match(lowered):
            if any(v in lowered for v in ("slrvb", "nord", "mini", "notion", "dnd")):
                continue
            kept.append(selector)
            continue

        if selector_allowed(selector):
            kept.append(selector)

    return ", ".join(kept) if kept else None


def strip_shell_declarations(selectors: str, body: str) -> str:
    """Remove app-shell layout properties from `html`/`body`/`:root` rules."""
    if not any(_ROOT_ELEMENT.match(s) for s in _split_selectors(selectors)):
        return body

    kept: list[str] = []
    for declaration in body.split(";"):
        name = declaration.split(":", 1)[0].strip().lower()
        if name and not name.startswith("--") and name in SHELL_PROPERTIES:
            continue
        kept.append(declaration)
    return ";".join(kept)


def _split_selectors(prelude: str) -> list[str]:
    """Split a selector list on top-level commas, respecting :is()/:not()."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in prelude:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def extract(css: str, *, strip_shell: bool = False) -> tuple[str, dict[str, int]]:
    stats = {"blocks": 0, "kept": 0, "dropped": 0, "at_rules_kept": 0}
    output: list[str] = []

    for block in scan(css):
        stats["blocks"] += 1

        if block.is_at_rule:
            prelude = " ".join(block.prelude.split())
            lowered = prelude.lower()
            if lowered.startswith(("@media print", "@supports not")):
                stats["dropped"] += 1
                continue
            if lowered.startswith("@font-face"):
                family = _FONT_FAMILY.search(block.body)
                name = family.group(1).strip().lower() if family else ""
                # Only self-contained fonts we actually use. A remote URL would
                # be blocked by CSP and fail silently at runtime.
                if "http" in block.body or name not in {
                    f.lower() for f in KEEP_FONT_FAMILIES
                }:
                    stats["dropped"] += 1
                    continue
                output.append(f"{prelude} {{{block.body}}}")
                stats["at_rules_kept"] += 1
                continue

            inner, inner_stats = extract(block.body, strip_shell=strip_shell)
            if inner.strip():
                output.append(f"{prelude} {{\n{inner}\n}}")
                stats["at_rules_kept"] += 1
            for key in ("kept", "dropped"):
                stats[key] += inner_stats[key]
            continue

        selectors = keep_rule(block.prelude, block.body)
        if selectors is None:
            stats["dropped"] += 1
            continue

        body = block.body
        if strip_shell:
            body = strip_shell_declarations(selectors, body)
        if not body.strip():
            stats["dropped"] += 1
            continue

        output.append(f"{selectors} {{{body}}}")
        stats["kept"] += 1

    return "\n".join(output), stats


def extract_cards(css: str) -> tuple[str, int]:
    """Cards.css, restricted to rules that target our dataview tables."""
    kept: list[str] = []
    for block in scan(css):
        if block.is_at_rule:
            inner, _ = extract_cards(block.body)
            if inner.strip():
                kept.append(f"{' '.join(block.prelude.split())} {{\n{inner}\n}}")
            continue
        selectors = keep_rule(block.prelude, block.body)
        if selectors:
            kept.append(f"{selectors} {{{block.body}}}")
    return "\n".join(kept), len(kept)


_VAR_DEF = re.compile(r"(--[\w-]+)\s*:")
_VAR_USE = re.compile(r"var\(\s*(--[\w-]+)\s*\)")
# Any reference, with or without a fallback -- used to detect self-reference,
# where `var(--x, 1rem)` is just as circular as `var(--x)`.
_VAR_REF = re.compile(r"var\(\s*(--[\w-]+)")


def rescue_variables(
    output: str, source: str, already_defined: frozenset[str] = frozenset()
) -> tuple[str, list[str], list[str]]:
    """Re-add variable definitions that filtering dropped but kept rules use.

    Filtering a variable-driven stylesheet fails quietly: keep
    `color: var(--text-accent)` while dropping the block that defined
    `--text-accent`, and the declaration resolves to nothing. Rather than
    hand-tuning the allowlist until that stops happening, this recovers the
    definitions from the source. Self-healing as the allowlist changes.

    Only references with no fallback matter; `var(--x, #fff)` already degrades.

    `already_defined` is what `src/styles/base.css` declares. Those must not be
    rescued: that file is the authoritative source for the application-level
    defaults, and hoisting the theme's value for one of them into `:root` here
    would silently outrank it -- `--link-weight` is 400 in the base layer and
    600 inside the theme's own rules for links nested in bold text, and
    recovering the latter bolded every link on the site.

    Returns `(block, recovered, unresolved)`. `unresolved` is the names no donor
    could supply; the caller reports them, because a variable that is used,
    undefined and unrecoverable is a silent hole in the output -- the
    declaration using it resolves to nothing and the element quietly inherits.
    Declare those by hand in `src/styles/base.css`.
    """
    defined = set(_VAR_DEF.findall(output)) | set(already_defined)
    missing = sorted(set(_VAR_USE.findall(output)) - defined)
    if not missing:
        return "", [], []

    recovered: list[str] = []
    unresolved: list[str] = []
    lines: list[str] = []
    for name in missing:
        # Take the first usable light-mode definition in source order; the theme
        # declares dark-mode overrides later.
        for match in re.finditer(re.escape(name) + r"\s*:\s*([^;{}]+);", source):
            preceding = source.rfind("}", 0, match.start())
            context = source[max(0, preceding) : match.start()].lower()
            if "theme-dark" in context:
                continue

            value = match.group(1).strip()
            # `--x: var(--x)` is legal where the theme writes it -- inside a
            # descendant rule it means "inherit the ancestor's value". Hoisted
            # into `:root` it becomes a cycle, which makes the property
            # guaranteed-invalid and takes every property computed from it down
            # with it. ITS Theme has exactly one (`--font-size`, in
            # `.tag-text.tag-text`), and it silently broke `--tag-size`,
            # `--tab-font-size` and `--popover-font-size`. Keep looking.
            if name in _VAR_REF.findall(value):
                continue

            lines.append(f"  {name}: {value};")
            recovered.append(name)
            break
        else:
            unresolved.append(name)

    if not lines:
        return "", [], unresolved
    block = ":root {\n" + "\n".join(lines) + "\n}"
    return block, recovered, unresolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config")
    parser.add_argument("-o", "--output", default="src/styles/vault-theme.css")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    config = config_mod.load_or_exit(args.config)
    root = config.vault_root
    obsidian = root / ".obsidian"

    theme_path = obsidian / "themes" / "ITS Theme" / "theme.css"
    cards_path = obsidian / "snippets" / "Cards.css"
    cards_shadow_path = obsidian / "snippets" / "Cards Shadow.css"
    custom_path = obsidian / "snippets" / "custom.css"

    sections: list[str] = [
        "/* GENERATED by scripts/extract_theme.py -- do not edit; re-run the script.",
        " *",
        " * This file is a DERIVATIVE WORK assembled from third-party sources.",
        " * Their terms travel with it -- see THIRD-PARTY.md for the full notices.",
        " *",
        " * ITS Theme (c) SlRvb -- GPL-2.0",
        " *     https://github.com/SlRvb/Obsidian--ITS-Theme",
        " *     Filtered to light mode and the `wotc-beyond` style. GPL-2.0 is",
        " *     copyleft: this file, being derived from it, is distributed under",
        " *     GPL-2.0, and the unminified form here is its source.",
        " *",
        " * Cards snippet (c) Stephan Ango (@kepano) -- MIT",
        " *     https://github.com/kepano/obsidian-minimal",
        " *",
        " * custom.css (c) the vault author -- included verbatim.",
        " *",
        " * This file sets values; it does not bind them to elements. That is",
        " * `src/styles/base.css`, which is original work, carries no",
        " * third-party terms, and loads BEFORE this file so the theme wins.",
        " */",
    ]

    sections.append("\n/* ===== font overrides ===== */")
    sections.append(FONT_OVERRIDES.strip())

    theme_css = vault.read_text(theme_path, root)
    theme_out, stats = extract(theme_css, strip_shell=True)
    sections.append("\n/* ===== ITS Theme (filtered) ===== */")
    sections.append(theme_out)
    print(
        f"theme.css   {len(theme_css):>9,} bytes -> {len(theme_out):>8,}  "
        f"({stats['kept']:,} rules kept, {stats['dropped']:,} dropped)"
    )

    for label, path in (("Cards.css", cards_path), ("Cards Shadow.css", cards_shadow_path)):
        if not path.exists():
            continue
        source = vault.read_text(path, root)
        out, count = extract_cards(source)
        if out.strip():
            sections.append(f"\n/* ===== {label} (filtered) ===== */")
            sections.append(out)
        print(f"{label:<11} {len(source):>9,} bytes -> {len(out):>8,}  ({count} rules kept)")

    custom_css = vault.read_text(custom_path, root)
    sections.append("\n/* ===== custom.css (verbatim) ===== */")
    sections.append(custom_css.strip())
    print(f"custom.css  {len(custom_css):>9,} bytes -> {len(custom_css):>8,}  (verbatim)")

    # The hand-authored base layer, which loads before this file and supplies the
    # application-level variable defaults the theme is written against.
    base_path = config.project_root / "src" / "styles" / "base.css"
    base_defined: frozenset[str] = frozenset()
    if base_path.exists():
        base_defined = frozenset(_VAR_DEF.findall(base_path.read_text(encoding="utf-8")))
        print(f"base.css      declares {len(base_defined)} variable(s)")

    rescue, recovered, unresolved = rescue_variables(
        "\n".join(sections),
        theme_css + "\n" + FONT_OVERRIDES,
        base_defined,
    )
    if rescue:
        sections.append("\n/* ===== variables recovered from dropped blocks ===== */")
        sections.append(rescue)
        print(f"\nrecovered {len(recovered)} variable(s) whose blocks were filtered out:")
        print("  " + ", ".join(recovered))

    if unresolved:
        # Used with no fallback, defined nowhere a donor could reach. The
        # declarations referencing these resolve to nothing, so the element
        # inherits instead -- no error, just a quietly different rendering.
        print(f"\nWARNING: {len(unresolved)} variable(s) used but undefined:")
        print("  " + ", ".join(unresolved))
        print("  Declare them in src/styles/base.css, which loads before this file.")

    destination = Path(args.output)
    if not destination.is_absolute():
        destination = config.project_root / destination
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = "\n".join(sections).rstrip() + "\n"
    destination.write_text(payload, encoding="utf-8", newline="\n")

    print(f"\nwrote {destination.relative_to(config.project_root)}  ({len(payload):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
