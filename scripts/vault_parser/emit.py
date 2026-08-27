"""Writing output.

This is the only module in the project that writes anything, and it refuses to
write anywhere except the three configured output directories -- each of which
was already validated at config load as being outside the vault. The check is
repeated here against resolved paths because `emit` is the last line of defence
for the read-only guarantee.

Output is byte-deterministic: frontmatter keys are sorted, YAML is dumped with
fixed options, and newlines are forced to `\\n`. Re-running the parser on an
unchanged vault must produce an empty `git diff`.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .model import Note

# Keys the parser adds. Kept out of the note's own namespace conceptually, but
# flattened into frontmatter so Astro's schema can validate them.
# Opening or closing line of a fenced block, for the block splitter.
_FENCE_LINE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")

GENERATED_KEYS = (
    "slug",
    "collection",
    "vaultPath",
    "breadcrumb",
    "isFolderNote",
    "sourceModified",
)


class EmitError(Exception):
    """A write was attempted outside the configured output directories."""


def assert_writable(path: Path, config: Config) -> Path:
    """Confirm `path` is inside a configured output dir and outside the vault."""
    resolved = _resolve_maybe_missing(path)

    if resolved == config.vault_root or config.vault_root in resolved.parents:
        raise EmitError(f"refusing to write inside the read-only vault: {resolved}")

    for output_dir in config.output_dirs:
        if resolved == output_dir or output_dir in resolved.parents:
            return resolved

    raise EmitError(
        f"refusing to write outside the configured output directories: {resolved}"
    )


def clean(config: Config, *, dry_run: bool = False) -> list[Path]:
    """Remove previous output. Only ever touches the three output directories."""
    removed: list[Path] = []
    for output_dir in config.output_dirs:
        assert_writable(output_dir, config)
        if not output_dir.exists():
            continue
        removed.append(output_dir)
        if not dry_run:
            _remove_tree(output_dir)
    return removed


def prune(expected: set[Path], config: Config, *, dry_run: bool = False) -> list[Path]:
    """Delete output files that this run did not produce.

    Without this, deleting or renaming a vault note leaves a stale page behind
    and the output stops being a pure function of the vault. Running it on
    every invocation is what lets `--clean` stay an occasional convenience
    rather than a requirement for correct output.
    """
    resolved = {_resolve_maybe_missing(path) for path in expected}
    stale: list[Path] = []

    for output_dir in config.output_dirs:
        if not output_dir.exists():
            continue
        for path in sorted(output_dir.rglob("*")):
            if path.is_dir() or path.resolve() in resolved:
                continue
            assert_writable(path, config)
            stale.append(path)
            if not dry_run:
                _remove_file(path)

    if not dry_run:
        _prune_empty_dirs(config)
    return stale


def _prune_empty_dirs(config: Config) -> None:
    for output_dir in config.output_dirs:
        if not output_dir.exists():
            continue
        # Deepest first, so a directory emptied by its children is caught.
        for path in sorted(output_dir.rglob("*"), key=lambda p: -len(p.parts)):
            if path.is_dir() and not any(path.iterdir()):
                assert_writable(path, config)
                _retry(path.rmdir)


def _remove_file(path: Path) -> None:
    _retry(lambda: path.unlink())


def _remove_tree(path: Path) -> None:
    _retry(lambda: shutil.rmtree(path, onexc=_clear_readonly))


def _clear_readonly(func, target, _exc) -> None:
    """rmtree error hook: drop the read-only bit and retry the operation."""
    os.chmod(target, stat.S_IWRITE)
    func(target)


def _retry(operation, attempts: int = 5, delay: float = 0.1) -> None:
    """Retry a filesystem operation through transient Windows locks.

    This project lives inside a Google Drive folder, where the sync client
    (and any open editor or indexer) intermittently holds handles on files the
    parser has just written. A failed delete there is a timing accident, not a
    permissions problem, so back off briefly rather than aborting the run.
    """
    for attempt in range(attempts):
        try:
            operation()
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (2**attempt))


def write_notes(notes: list[Note], config: Config, *, dry_run: bool = False) -> list[Path]:
    """Write every published note to `<content>/<collection>/<slug>.md`."""
    written: list[Path] = []
    for note in sorted(notes, key=lambda n: n.out_path):
        destination = config.content_dir / note.out_path
        assert_writable(destination, config)
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write_text(destination, render(note, config))
        written.append(destination)
    return written


def write_generated(
    artifacts: dict[str, Any], config: Config, *, dry_run: bool = False
) -> list[Path]:
    """Write sidecar JSON produced by `finalize()` modules."""
    written: list[Path] = []
    for name in sorted(artifacts):
        destination = config.generated_dir / name
        assert_writable(destination, config)
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                artifacts[name], ensure_ascii=False, indent=2, sort_keys=True
            )
            _write_text(destination, payload + "\n")
        written.append(destination)
    return written


def _write_text(destination: Path, text: str) -> None:
    _retry(lambda: destination.write_text(text, encoding="utf-8", newline="\n"))


def render(note: Note, config: Config | None = None) -> str:
    """Serialise a note to markdown with sorted, Astro-safe frontmatter."""
    data = build_frontmatter(note)
    dumped = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
        width=10_000,  # never wrap; wrapping would churn diffs
    ).rstrip("\n")

    body = _body(note, config.i18n.default if config else "")
    return f"---\n{dumped}\n---\n\n{body}\n" if body else f"---\n{dumped}\n---\n"


def _body(note: Note, default: str) -> str:
    """The note's body, with every language it carries.

    A note written in one language emits exactly what it always did -- no
    wrapper, no attribute -- so the overwhelming majority of the site is
    unchanged and stays diffable.
    """
    if len(note.lang_bodies) <= 1:
        return _clean(note.body)
    return _merge_languages(note, default)


def _merge_languages(note: Note, default: str) -> str:
    """Interleave the per-language bodies, emitting shared content once.

    The naive shape -- one `<div>` per language, each holding that language's
    whole body -- duplicates everything the languages have in common. For a
    `:::lang all` block that is not merely wasteful but wrong: the Tambler map
    came out three times, so pan/zoom was remembered per language rather than
    per page, and the two hidden copies initialised while `display: none`,
    measured their container as zero, and opened at the wrong zoom.

    So content identical in every language is emitted once, unwrapped, and only
    what actually differs gets a language wrapper. Sharing is decided by
    comparing the *rendered* output rather than by tracking source blocks,
    which makes it correct by construction -- if something is identical in
    every language, showing it always is what each language was going to show
    anyway. It also handles what the source structure cannot: an `all` block
    holding both a map and an infobox, where the map really is shared and the
    infobox really does differ.

    **Comparison is per block, never per line.** A line-level diff will happily
    match an infobox's `<div>` wrapper in every language while the rows inside
    differ, and then emit the wrapper once with three language `<div>`s nested
    inside it -- structurally broken HTML. Blocks are atomic, so a construct is
    either shared whole or not at all.

    Degrades safely: when no block matches, every one is language-specific and
    the result is the naive shape again.
    """
    codes = [code for code in note.languages if code in note.lang_bodies]
    if not codes:
        return _clean(note.body)

    spine_code = default if default in codes else codes[0]
    others = [code for code in codes if code != spine_code]
    blocks = {code: _blocks(_clean(note.lang_bodies[code])) for code in codes}
    spine = blocks[spine_code]

    # spine block index -> index of the identical block in each other language.
    matched: dict[str, dict[int, int]] = {}
    for code in others:
        pairs: dict[int, int] = {}
        for run in SequenceMatcher(None, spine, blocks[code], autojunk=False).get_matching_blocks():
            for offset in range(run.size):
                pairs[run.a + offset] = run.b + offset
        matched[code] = pairs

    shared = [i for i in range(len(spine)) if all(i in matched[code] for code in others)]
    if not shared:
        return _wrapped(codes, blocks)

    chunks: list[str] = []
    cursor = {code: 0 for code in codes}

    for index in shared:
        gap = {spine_code: spine[cursor[spine_code]:index]}
        for code in others:
            gap[code] = blocks[code][cursor[code]:matched[code][index]]
        chunks.append(_wrapped(codes, gap))

        chunks.append(spine[index])

        cursor[spine_code] = index + 1
        for code in others:
            cursor[code] = matched[code][index] + 1

    tail = {spine_code: spine[cursor[spine_code]:]}
    for code in others:
        tail[code] = blocks[code][cursor[code]:]
    chunks.append(_wrapped(codes, tail))

    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def _blocks(text: str) -> list[str]:
    """Split into top-level blocks on blank lines, keeping fences whole.

    A fenced block may contain blank lines of its own, and splitting one in
    half would let the halves be shared independently -- emitting an opening
    fence with no closer.
    """
    out: list[str] = []
    current: list[str] = []
    fence: tuple[str, int] | None = None

    for line in text.split("\n"):
        match = _FENCE_LINE.match(line)
        if match:
            marker, rest = match.group(1), match.group(2)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1] and not rest.strip():
                fence = None

        if not line.strip() and fence is None:
            if current:
                out.append("\n".join(current))
                current = []
        else:
            current.append(line)

    if current:
        out.append("\n".join(current))
    return out


def _wrapped(codes: list[str], segments: dict[str, list[str]]) -> str:
    """One `<div>` per language that has something to say here.

    The wrapper is a `<div>`, which opens an HTML block that swallows
    everything up to the next blank line -- hence the blank lines around the
    content, without which the markdown inside would never parse.
    """
    parts: list[str] = []
    for code in codes:
        text = "\n\n".join(segments.get(code, [])).strip("\n")
        if not text.strip():
            continue
        parts.append(
            f'<div class="lang-section" data-lang="{code}">'
            + "\n\n" + text + "\n\n</div>"
        )
    return "\n\n".join(parts)


def _clean(body: str) -> str:
    return body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def build_frontmatter(note: Note) -> dict[str, Any]:
    """Merge the note's own frontmatter with the keys the parser derives."""
    data: dict[str, Any] = {}

    for key, value in note.frontmatter.items():
        if key in GENERATED_KEYS:
            continue  # never let vault content shadow a derived key
        data[key] = _plain(value)

    data["title"] = note.title
    data["slug"] = note.slug
    data["collection"] = note.collection
    data["vaultPath"] = note.vault_path
    data["breadcrumb"] = list(note.breadcrumb)
    data["isFolderNote"] = note.is_folder_note
    data["sourceModified"] = note.source_modified
    data["publish"] = True  # only published notes are ever emitted
    # Which languages this note actually carries content for. The site reads
    # it to decide between showing a translation and showing the default
    # language with a "not translated yet" notice.
    if note.languages:
        data["languages"] = list(note.languages)

    if note.aliases:
        data["aliases"] = list(note.aliases)
    else:
        data.pop("aliases", None)

    if note.tags:
        data["tags"] = list(note.tags)
    else:
        data.pop("tags", None)
    data.pop("tag", None)

    # Drop keys with no value so the schema can treat them as absent rather
    # than as an explicit null.
    return {key: value for key, value in data.items() if value is not None}


def _plain(value: Any) -> Any:
    """Reduce to types `yaml.safe_dump` handles without tags."""
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return str(value)


def _resolve_maybe_missing(path: Path) -> Path:
    existing = Path(path)
    tail: list[str] = []
    while not existing.exists() and existing.parent != existing:
        tail.append(existing.name)
        existing = existing.parent
    return existing.resolve().joinpath(*reversed(tail))
