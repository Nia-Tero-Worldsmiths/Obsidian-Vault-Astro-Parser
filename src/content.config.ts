import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

/**
 * Schemas for the collections emitted by `scripts/vault_parser`.
 *
 * Everything under `src/content/` is GENERATED. Do not edit it by hand -- run
 * the parser instead. The source of truth is the Obsidian vault at
 * `src/vault`, which is read-only.
 *
 * These schemas are deliberately permissive about vault-authored fields and
 * strict about parser-derived ones. A vault of 239 hand-written notes carries
 * dozens of ad-hoc properties (`elan`, `pathbuilderId`, `cargoOrg`, ...) that
 * we do not want to enumerate or break the build over; but if the parser ever
 * stops emitting `slug` or `breadcrumb`, that is a bug and the build should
 * fail loudly.
 */

/** Fields the parser derives. Required -- their absence means a parser bug. */
const generated = {
  title: z.string(),
  slug: z.string(),
  collection: z.string(),
  vaultPath: z.string(),
  breadcrumb: z.array(z.string()),
  isFolderNote: z.boolean(),
  sourceModified: z.string(),
  publish: z.literal(true), // only published notes are ever emitted
};

/** Common vault-authored fields worth typing, all optional. */
const authored = {
  NoteType: z.string().optional(),
  aliases: z.array(z.string()).optional(),
  tags: z.array(z.string()).optional(),
  imagen: z.string().optional(),
  // Obsidian accepts both the list and single-string forms.
  cssclasses: z.union([z.string(), z.array(z.string())]).optional(),
  // Language codes the note carries content for, in configured order.
  // Optional because the parser omits it when `i18n` is switched off;
  // absent means the note is monolingual in the default language.
  languages: z.array(z.string()).optional(),
};

// `.passthrough()` keeps every other vault property available on `entry.data`
// without having to declare it here.
const noteSchema = z.object({ ...generated, ...authored }).passthrough();

const collectionFor = (name: string) =>
  defineCollection({
    loader: glob({ pattern: "**/*.md", base: `./src/content/${name}` }),
    schema: noteSchema,
  });

export const collections = {
  atlas: collectionFor("atlas"),
  encyclopedia: collectionFor("encyclopedia"),
  prosa: collectionFor("prosa"),
};
