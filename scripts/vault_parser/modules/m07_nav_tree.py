"""Module 7 -- Navigation tree.

Rebuilds the vault's folder hierarchy from each note's `breadcrumb` and emits
it as `src/generated/nav-tree.json`, which the sidebar renders on every page.
This is the site's primary navigation, since URLs are flat and carry no
hierarchy of their own.

Folder notes follow the vault's `folder-notes` plugin configuration
(`folderNoteName: "{{folder_name}}"`, `storageLocation: insideFolder`,
`hideFolderNote: true`): a note named after its own parent folder is that
folder's destination, not one of its children. `Luma/Luma.md` therefore makes
the `Luma` folder itself clickable and does not appear inside it -- listing it
as a child would show `Luma > Luma`.

Pruning follows from building the tree out of published notes only:

* a folder whose entire subtree is unpublished never gets created;
* a folder whose folder note is unpublished but which still has published
  descendants survives as a non-clickable grouping, so the hierarchy stays
  intact rather than collapsing children up a level.

Runs in `finalize` rather than `transform`: this is one sitewide artifact, not
a per-note edit.
"""

from __future__ import annotations

from typing import Any

from ..model import Note, VaultContext
from ..slugs import natural_key
from .base import TransformModule


class NavTreeModule(TransformModule):
    name = "nav_tree"
    order = 70
    summary = "Build the folder-note-aware navigation tree as nav-tree.json"
    stub = False

    ARTIFACT = "nav-tree.json"

    def finalize(self, ctx: VaultContext) -> None:
        published = ctx.published_notes()
        root: dict[str, Any] = _new_folder("", "")

        for note in published:
            self._place(note, root)

        tree = _serialise(root)

        # Two shape simplifications, applied after the faithful tree is built
        # so each is a self-contained pass over a plain structure rather than a
        # special case threaded through placement.
        if self.options.get("flatten_leaf_folder_notes", True):
            flattened = _flatten_leaves(tree)
            self.count("leaf folder notes flattened", flattened)

        if self.options.get("collapse_chains", True):
            collapsed = _collapse_chains(tree, top_level=True)
            self.count("connector chains collapsed", collapsed)

        ctx.generated[self.ARTIFACT] = tree

        self.count("notes placed", len(published))
        self.count("folders", _count(tree, "folder"))
        self.count("folder notes", _count_folder_notes(tree))
        self._report(tree, published)

    def _place(self, note: Note, root: dict[str, Any]) -> None:
        """Insert a note into the tree, attaching folder notes to their folder.

        The trail comes from `vault_path`, not `breadcrumb`. `breadcrumb` is a
        *display* value -- it deliberately omits a folder note's own folder, so
        the trail above the title does not repeat it -- and reading it here
        placed every folder note one level too high, silently overwriting
        sibling folder notes that landed on the same parent.
        """
        trail = note.vault_path.split("/")[:-1]

        if note.is_folder_note and trail:
            # The note *is* the folder it lives in; walk to that folder and
            # attach, rather than descending into it.
            folder = _descend(root, trail)
            existing = folder.get("note")
            if existing is not None and existing is not note:
                # Two notes claiming one folder means the trail is wrong: only
                # one file can be named after its parent. Assigning silently is
                # how a placement bug hid here once already.
                self.warn(
                    f"two folder notes for `{'/'.join(trail)}`: "
                    f"{existing.vault_path} and {note.vault_path}"
                )
                self.count("folder note collisions")
            folder["note"] = note
            return

        folder = _descend(root, trail)
        folder["notes"].append(note)

    # -- reporting -------------------------------------------------------

    def _report(self, tree: dict[str, Any], published: list[Note]) -> None:
        headless = _headless_folders(tree)
        if not headless:
            return

        lines = [
            "Folders that appear in the sidebar but are not clickable, because",
            "their folder note is missing or unpublished. Publishing the note",
            "named after the folder makes the folder itself a destination.",
            "",
        ]
        for path, count in sorted(headless, key=lambda item: (-item[1], item[0])):
            lines.append(f"  {count:3} published descendant(s)  {path}")
        self.section("Folders without a published folder note", lines)


# -- tree construction ----------------------------------------------------


def _new_folder(name: str, path: str) -> dict[str, Any]:
    return {"name": name, "path": path, "folders": {}, "notes": [], "note": None}


def _descend(root: dict[str, Any], trail: list[str]) -> dict[str, Any]:
    current = root
    for segment in trail:
        child = current["folders"].get(segment)
        if child is None:
            path = f"{current['path']}/{segment}" if current["path"] else segment
            child = _new_folder(segment, path)
            current["folders"][segment] = child
        current = child
    return current


def _serialise(folder: dict[str, Any]) -> dict[str, Any]:
    """Convert the working structure into the JSON the sidebar consumes."""
    children: list[dict[str, Any]] = []

    for sub in folder["folders"].values():
        children.append(_serialise(sub))

    for note in folder["notes"]:
        children.append(
            {
                "type": "note",
                "title": note.title,
                "slug": note.slug,
                "url": note.url,
                "path": note.vault_path,
            }
        )

    # Folders first, then notes -- matching Obsidian's file explorer -- each
    # group ordered naturally so `2 - Foo` precedes `10 - Bar`.
    children.sort(key=lambda item: (item["type"] != "folder", natural_key(item["title"])))

    node: dict[str, Any] = {
        "type": "folder",
        "title": folder["name"],
        "path": folder["path"],
        "children": children,
    }

    note = folder["note"]
    if note is not None:
        node["slug"] = note.slug
        node["url"] = note.url
        node["notePath"] = note.vault_path

    return node


# -- introspection --------------------------------------------------------


def _flatten_leaves(node: dict[str, Any]) -> int:
    """Turn a folder whose only content is its own folder note into a note.

    Many folders exist only to hold material that never leaves its author's
    machine -- the vault's `_*` spoiler directories are git-ignored, so on
    another checkout their parent arrives as a folder containing nothing but
    its own folder note. Rendering that as an expandable folder with a single
    child that *is* the folder is noise, and it deepens every path beneath it.

    Bottom-up, so a chain of such folders collapses in one pass.
    """
    changed = 0
    children = node.get("children", [])

    for index, child in enumerate(children):
        if child.get("type") != "folder":
            continue

        changed += _flatten_leaves(child)

        # A leaf worth flattening: it is a destination in its own right and
        # has nothing else under it.
        if child.get("slug") and not child.get("children"):
            children[index] = {
                "type": "note",
                "title": child["title"],
                "slug": child["slug"],
                "url": child["url"],
                "path": child.get("notePath", child.get("path", "")),
                "wasFolder": True,
            }
            changed += 1

    return changed


def _collapse_chains(node: dict[str, Any], top_level: bool = False) -> int:
    """Merge a pass-through folder into its single child folder.

    `Pandysia > Juutei > Regiones > Ryuutei > Ryuujou > Personas importantes`
    is six clicks to reach one note. Where each folder in that run is a pure
    connector -- no folder note of its own, no notes directly inside, exactly
    one child folder -- the run carries no information that the joined label
    does not, so it becomes a single row.

    Deliberately conservative in two ways: a folder with its own note is a real
    destination and is never merged away, and collection roots are left intact
    so `World Atlas` stays a distinct entry point rather than being welded to
    `Plano Terrenal`.
    """
    changed = 0
    children = node.get("children", [])

    for index, child in enumerate(children):
        if child.get("type") != "folder":
            continue

        # Collapse this child into its descendants first, then see whether the
        # child itself can be absorbed.
        changed += _collapse_chains(child)

        if top_level:
            continue  # never weld a collection root to its child

        merged = child
        while _is_connector(merged):
            only = merged["children"][0]
            only["title"] = f"{merged['title']} / {only['title']}"
            only["collapsedFrom"] = merged.get("collapsedFrom", []) + [merged["title"]]
            merged = only
            changed += 1
        children[index] = merged

    return changed


def _is_connector(folder: dict[str, Any]) -> bool:
    """A folder that only leads somewhere: one child folder and nothing else."""
    if folder.get("slug"):
        return False  # it is a destination itself
    children = folder.get("children", [])
    return len(children) == 1 and children[0].get("type") == "folder"


def _count(node: dict[str, Any], kind: str) -> int:
    total = 1 if node.get("type") == kind and node.get("path") else 0
    for child in node.get("children", ()):
        total += _count(child, kind)
    return total


def _count_folder_notes(node: dict[str, Any]) -> int:
    total = 1 if node.get("type") == "folder" and node.get("slug") else 0
    for child in node.get("children", ()):
        total += _count_folder_notes(child)
    return total


def _headless_folders(node: dict[str, Any]) -> list[tuple[str, int]]:
    """Folders with published descendants but no published folder note."""
    found: list[tuple[str, int]] = []

    for child in node.get("children", ()):
        if child.get("type") != "folder":
            continue
        found.extend(_headless_folders(child))
        if not child.get("slug"):
            found.append((child["path"], _descendant_notes(child)))

    return found


def _descendant_notes(node: dict[str, Any]) -> int:
    total = 0
    for child in node.get("children", ()):
        if child.get("type") == "note":
            total += 1
        else:
            total += _descendant_notes(child) + (1 if child.get("slug") else 0)
    return total
