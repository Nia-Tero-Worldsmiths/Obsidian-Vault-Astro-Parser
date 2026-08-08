"""Module 9 -- Link graph.

Emits `src/generated/graph.json`: the note-to-note link graph, per-note
backlinks, and the appearance settings copied from the vault's own
`.obsidian/graph.json` so the site's graph looks like the one in Obsidian
rather than a generic force diagram.

From that config it honours the five path-based colour groups, `showOrphans`
(notes with no links at all are hidden by default), `showArrow`, and the force
parameters (`centerStrength`, `repelStrength`, `linkStrength`, `linkDistance`).

Edges are recomputed from `raw_body` and frontmatter rather than read out of
the transformed text. By the time this runs, Module 2 has rewritten every
wikilink into an `<a href>`, and Module 8 has replaced map fences wholesale --
parsing that back out would be both fragile and lossy. Working from the
untransformed source makes the module independent of what ran before it, so
`--only graph` produces the same graph as a full run.

Runs in `finalize`: one sitewide artifact, not a per-note edit.
"""

from __future__ import annotations

from typing import Any

from .. import linking
from ..linking import LinkKind
from ..model import Note, VaultContext
from .base import TransformModule

GRAPH_CONFIG = ".obsidian/graph.json"

#: Fallback palette if the vault has no graph config.
DEFAULT_FORCES = {
    "centerStrength": 0.5,
    "repelStrength": 10.0,
    "linkStrength": 1.0,
    "linkDistance": 30.0,
}


class GraphModule(TransformModule):
    name = "graph"
    order = 90
    summary = "Serialise the note link graph and backlinks as graph.json"
    stub = False

    ARTIFACT = "graph.json"

    def finalize(self, ctx: VaultContext) -> None:
        published = ctx.published_notes()
        by_slug = {note.slug: note for note in published}

        edges: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        outbound: dict[str, set[str]] = {n.slug: set() for n in published}
        inbound: dict[str, set[str]] = {n.slug: set() for n in published}

        for note in published:
            # `sorted` is not cosmetic: `_targets` returns a set, and Python
            # randomises string-set iteration per process, so iterating it
            # directly made the emitted edge order differ between otherwise
            # identical runs.
            for target in sorted(self._targets(note, ctx)):
                if target not in by_slug or target == note.slug:
                    continue
                outbound[note.slug].add(target)
                inbound[target].add(note.slug)
                key = (note.slug, target)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": note.slug, "target": target})

        config = self._config(ctx)
        groups = _colour_groups(config)
        show_orphans = bool(config.get("showOrphans", False))

        nodes: list[dict[str, Any]] = []
        for note in sorted(published, key=lambda n: n.slug):
            degree = len(outbound[note.slug]) + len(inbound[note.slug])
            if degree == 0 and not show_orphans:
                self.count("orphans hidden")
                continue
            nodes.append(
                {
                    "id": note.slug,
                    "title": note.title,
                    "url": note.url,
                    "collection": note.collection,
                    "degree": degree,
                    "color": _colour_for(note, groups),
                }
            )

        visible = {node["id"] for node in nodes}
        edges = sorted(
            (e for e in edges if e["source"] in visible and e["target"] in visible),
            key=lambda e: (e["source"], e["target"]),
        )

        ctx.generated[self.ARTIFACT] = {
            "nodes": nodes,
            "edges": edges,
            "backlinks": {
                slug: sorted(sources) for slug, sources in sorted(inbound.items()) if sources
            },
            "settings": {
                "showArrow": bool(config.get("showArrow", False)),
                "nodeSizeMultiplier": float(config.get("nodeSizeMultiplier", 1.0)),
                "lineSizeMultiplier": float(config.get("lineSizeMultiplier", 1.0)),
                "forces": {
                    key: float(config.get(key, default))
                    for key, default in DEFAULT_FORCES.items()
                },
            },
        }

        self.count("nodes", len(nodes))
        self.count("edges", len(edges))
        self.count("colour groups", len(groups))
        self._report(nodes, inbound, outbound)

    # -- data ------------------------------------------------------------

    def _targets(self, note: Note, ctx: VaultContext) -> set[str]:
        """Slugs this note links to, from prose and from frontmatter alike."""
        found: set[str] = set()
        refs = list(linking.parse_refs(note.raw_body)) + list(note.frontmatter_refs)
        for ref in refs:
            if not ref.target:
                continue
            resolution = linking.resolve(ref, ctx)
            if resolution.kind is LinkKind.PUBLISHED and resolution.note is not None:
                found.add(resolution.note.slug)
        return found

    def _config(self, ctx: VaultContext) -> dict[str, Any]:
        import json

        from .. import vault

        path = ctx.config.vault_root / GRAPH_CONFIG
        if not path.exists():
            self.warn("no .obsidian/graph.json; using default appearance")
            return {}
        try:
            return json.loads(vault.read_text(path, ctx.config.vault_root))
        except (json.JSONDecodeError, OSError) as exc:
            self.warn(f"could not read graph.json ({exc}); using defaults")
            return {}

    # -- report ----------------------------------------------------------

    def _report(self, nodes, inbound, outbound) -> None:
        if not nodes:
            return
        ranked = sorted(nodes, key=lambda n: (-n["degree"], n["id"]))[:8]
        lines = [
            "Most connected notes in the published graph:",
            "",
        ]
        for node in ranked:
            slug = node["id"]
            lines.append(
                f"  {node['degree']:3} links  {node['title']}"
                f"  ({len(inbound[slug])} in / {len(outbound[slug])} out)"
            )
        self.section("Link graph", lines)


def _colour_groups(config: dict[str, Any]) -> list[tuple[str, str]]:
    """`colorGroups` reduced to (path prefix, hex colour) pairs.

    Obsidian stores the query as a search expression and the colour as a packed
    integer. Only `path:"..."` queries are understood; anything else is skipped
    rather than guessed at.
    """
    groups: list[tuple[str, str]] = []
    for entry in config.get("colorGroups", ()):
        query = str(entry.get("query", "")).strip()
        if not query.startswith("path:"):
            continue
        prefix = query[len("path:"):].strip().strip('"').strip()
        rgb = (entry.get("color") or {}).get("rgb")
        if not prefix or rgb is None:
            continue
        groups.append((prefix, f"#{int(rgb) & 0xFFFFFF:06x}"))
    return groups


def _colour_for(note: Note, groups: list[tuple[str, str]]) -> str | None:
    """First matching group wins, as in Obsidian."""
    path = note.vault_path
    for prefix, colour in groups:
        if path.startswith(prefix):
            return colour
    return None
