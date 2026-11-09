/**
 * The published index, for consumers that are not this site.
 *
 * `src/generated/nav-tree.json` is already the right shape -- a nested tree of
 * folders and notes carrying `slug`, `title`, `url` and the vault path -- but
 * it lives under `output.generated`, which is build input and is never served.
 * This endpoint is the one line between the two: it re-serves that artifact at
 * a stable URL, next to the few things a consumer needs and the tree does not
 * carry.
 *
 * Written for the Foundry VTT module, which reconciles Foundry journal entries
 * against the published set. That reconciliation has one hard requirement: a
 * note must be recognisable as *the same note* across runs, because the
 * identities it derives are referenced by hand elsewhere in a campaign and
 * cannot be allowed to churn. Hence `slug` is the key, and hence
 * `contentVersion`, which lets a consumer tell "nothing has changed" from "the
 * whole vault moved" without diffing 48 notes.
 *
 * Deliberately not an Astro page: there is no markup, and nothing here should
 * go through a layout. In a static build it prerenders to `/foundry-index.json`
 * like any other file.
 */
import type { APIRoute } from "astro";
import { getCollection } from "astro:content";

import { DEFAULT_LANGUAGE, LANGUAGES } from "../i18n/site";

export const prerender = true;

/* Globbed rather than imported, the way `BaseLayout` and `i18n/site.ts` do it:
 * disabling the `nav_tree` module should leave an index with no tree, not a
 * build that fails on a missing file. */
const loadedTree = Object.values(
  import.meta.glob<{ children: unknown[] }>("../generated/nav-tree.json", {
    eager: true,
    import: "default",
  }),
)[0] ?? { children: [] };

/**
 * FNV-1a, 32 bits, as eight hex characters.
 *
 * A fingerprint, not a signature: it only has to change when the input does.
 * Chosen over `crypto.subtle` because the consumer has to be able to reproduce
 * it, and a Foundry server reached over plain HTTP on a LAN address is not a
 * secure context -- `crypto.subtle` is simply absent there. Ten lines of
 * arithmetic has no such precondition and ports to any language in minutes.
 */
function fnv1a(input: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    // The 32-bit FNV prime, 16777619, by shifts -- `*` would lose precision
    // once the product passes 2^53.
    hash +=
      (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
    hash >>>= 0;
  }
  return hash.toString(16).padStart(8, "0");
}

export const GET: APIRoute = async () => {
  const collections = ["atlas", "encyclopedia", "prosa"] as const;

  const entries = (
    await Promise.all(collections.map((name) => getCollection(name)))
  ).flat();

  /* Sorted by slug so the fingerprint depends on the content and not on the
   * order the collections happened to load in. */
  const notes = entries
    .map((entry) => ({
      slug: entry.data.slug,
      title: entry.data.title,
      url: `/${entry.data.slug}/`,
      collection: entry.data.collection,
      breadcrumb: entry.data.breadcrumb,
      vaultPath: entry.data.vaultPath,
      isFolderNote: entry.data.isFolderNote,
      /* Absent means monolingual in the default language -- the same
       * convention the note schema documents. Spelled out here so a consumer
       * never has to know that. */
      languages: entry.data.languages ?? [DEFAULT_LANGUAGE],
      sourceModified: entry.data.sourceModified,
    }))
    .sort((a, b) => (a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0));

  const lastModified = notes.reduce(
    (latest, note) => (note.sourceModified > latest ? note.sourceModified : latest),
    "",
  );

  /* Three parts, each readable on its own: how many notes, when the newest
   * edit was, and a hash over every slug and its own timestamp. The first two
   * make a changed version legible to a human at a glance; the third is what
   * actually makes it correct, catching a rename or a rollback that leaves the
   * count and the newest date untouched. */
  const contentVersion = [
    notes.length,
    lastModified.replace(/[-:]/g, "").replace(/\+.*$/, ""),
    fnv1a(notes.map((note) => `${note.slug}@${note.sourceModified}`).join("\n")),
  ].join("-");

  const body = {
    /* Bumped only by a breaking change to this shape, so a consumer can refuse
     * an index it does not understand instead of half-reading it. */
    schema: 1,
    contentVersion,
    lastModified,
    defaultLanguage: DEFAULT_LANGUAGE,
    languages: LANGUAGES,
    notes,
    /* The parser's own artifact, verbatim. Folder order here is the order the
     * site's sidebar shows, which is the only place the vault's own sequence
     * survives -- `notes` above is sorted for the fingerprint's sake. */
    tree: loadedTree,
  };

  return new Response(JSON.stringify(body, null, 2), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      /* Production already answers `*` for everything (a Cloudflare Pages
       * default, verified against the deployed site). Stating it here is for
       * `astro dev`, which does not, and where a consumer testing against a
       * local build would otherwise be blocked by CORS for reasons that have
       * nothing to do with its own code. */
      "access-control-allow-origin": "*",
    },
  });
};
