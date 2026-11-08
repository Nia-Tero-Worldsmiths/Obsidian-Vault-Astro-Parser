/**
 * Text for the pages the parser does not write, in every language the site
 * serves.
 *
 * A note's page gets its words from the vault, and its language bar from the
 * codes the parser wrote into its frontmatter. `/`, `/galeria` and `/grafo`
 * have no note behind them, so their chrome was hardcoded Spanish and they
 * offered no way to switch. This is where their words live instead.
 *
 * `LANGUAGES` comes from `site.json`, written by the `site_index` module out
 * of `i18n.languages` minus `i18n.ignore`. Reading it rather than listing the
 * codes here is the point: a hand-written list can only drift one way, into
 * offering a reader a language the notes were never emitted in.
 *
 * The tables below carry `en` even though the site does not serve it today.
 * That is not dead weight -- it is what makes clearing `i18n.ignore` a
 * one-line change instead of a translation job, and the pages only ever offer
 * what `LANGUAGES` holds.
 */

export interface Language {
  code: string;
  label: string;
}

interface SiteSettings {
  defaultLanguage: string;
  languages: Language[];
}

/* Globbed rather than imported directly, the way the nav tree and the graph
 * are: disabling `site_index` should leave a monolingual site, not a build
 * that fails on a missing file. */
const loaded = Object.values(
  import.meta.glob<SiteSettings>("../generated/site.json", {
    eager: true,
    import: "default",
  }),
)[0] ?? { defaultLanguage: "es", languages: [{ code: "es", label: "Español" }] };

export const LANGUAGES: Language[] = loaded.languages;
export const DEFAULT_LANGUAGE: string = loaded.defaultLanguage;
export const LANGUAGE_CODES: string[] = LANGUAGES.map((language) => language.code);

/** A per-language string, as `Localized.astro` and the page scripts take it. */
export type Localized = Record<string, string>;

/**
 * Shown when a reader asks for a language a note does not have.
 *
 * Written in the language being asked for -- a reader who switched to
 * Japanese and got Spanish needs the reason in Japanese. Lives here rather
 * than in `LanguageBar` so that all of the site's own words are in one file.
 */
export const NOTICES: Localized = {
  es: "Esta nota todavía no está traducida. Se muestra en español.",
  ja: "このノートはまだ翻訳されていません。スペイン語で表示しています。",
  en: "This note has not been translated yet. Showing the Spanish original.",
};

export const LANGUAGE_PICKER_LABEL: Localized = {
  es: "Idioma",
  ja: "言語",
  en: "Language",
};

/* ------------------------------------------------------------------ *
 * / -- the provisional index
 * ------------------------------------------------------------------ */

export const INDEX = {
  /** `{n}` is replaced with the note count. */
  intro: {
    es: "{n} notas publicadas desde el vault.",
    ja: "ヴォルトから公開されているノート{n}件。",
    en: "{n} notes published from the vault.",
  } as Localized,
  /* The three content collections, by their key in `collections:`. */
  collections: {
    atlas: { es: "Atlas", ja: "アトラス", en: "Atlas" } as Localized,
    encyclopedia: { es: "Enciclopedia", ja: "百科事典", en: "Encyclopedia" } as Localized,
    prosa: { es: "Prosa", ja: "散文", en: "Prose" } as Localized,
  },
};

/* ------------------------------------------------------------------ *
 * /grafo
 * ------------------------------------------------------------------ */

export const GRAPH = {
  heading: { es: "Grafo", ja: "グラフ", en: "Graph" } as Localized,
  intro: {
    es: "Conexiones entre las {n} notas publicadas. Los colores siguen los grupos definidos en el vault. Arrastra un nodo para moverlo, la rueda para acercar.",
    ja: "公開されている{n}件のノート間のつながり。色はヴォルトで定義されたグループに従います。ノードはドラッグで移動でき、ホイールで拡大縮小します。",
    en: "Connections between the {n} published notes. Colours follow the groups defined in the vault. Drag a node to move it, scroll to zoom.",
  } as Localized,
};

/* ------------------------------------------------------------------ *
 * /galeria
 * ------------------------------------------------------------------ */

export const GALLERY = {
  heading: { es: "Galería", ja: "ギャラリー", en: "Gallery" } as Localized,
  intro: {
    es: "{n} imágenes de la obra original del proyecto y de material con licencia para publicarse. Cada una indica su autoría y en qué notas aparece. Las imágenes generadas por IA y las de terceros sin permiso no se muestran aquí.",
    ja: "プロジェクトのオリジナル作品と公開許諾のある素材、あわせて{n}点。各画像に作者と掲載ノートを示しています。AI生成画像および許諾のない第三者の画像は掲載していません。",
    en: "{n} images from the project's original artwork and from material licensed for publication. Each one names its author and the notes it appears on. AI-generated images and third-party images without permission are not shown here.",
  } as Localized,

  searchLabel: {
    es: "Buscar por nombre, autoría o nota",
    ja: "名前・作者・ノートで検索",
    en: "Search by name, author or note",
  } as Localized,

  /** `{shown}` and `{total}` are replaced with the counts. */
  count: {
    es: "{shown} de {total}",
    ja: "{total}件中{shown}件",
    en: "{shown} of {total}",
  } as Localized,

  /** Shown in place of the grid when nothing matches. */
  empty: {
    es: "Ninguna imagen coincide con el filtro.",
    ja: "条件に一致する画像はありません。",
    en: "No images match the filter.",
  } as Localized,

  /* ── The per-note filter ── */

  filterLabel: {
    es: "Filtrar por nota",
    ja: "ノートで絞り込む",
    en: "Filter by note",
  } as Localized,
  /** The button's text with nothing picked. */
  filterNone: {
    es: "Todas las notas",
    ja: "すべてのノート",
    en: "All notes",
  } as Localized,
  /** With more than one picked. `{n}` is how many. */
  filterSome: {
    es: "{n} notas seleccionadas",
    ja: "{n}件のノートを選択",
    en: "{n} notes selected",
  } as Localized,
  filterClear: {
    es: "Quitar selección",
    ja: "選択を解除",
    en: "Clear selection",
  } as Localized,
  /** The two entries that are not notes. */
  bucketUnused: { es: "Sin usar", ja: "未使用", en: "Unused" } as Localized,
  bucketStandIn: { es: "Suplente", ja: "代替画像", en: "Stand-in" } as Localized,

  /* ── Card states ── */

  usageNone: {
    es: "No aparece en ninguna nota",
    ja: "どのノートにも掲載なし",
    en: "Not shown on any note",
  } as Localized,
  usageStandIn: {
    es: "Suplente de las notas sin retrato",
    ja: "肖像のないノートの代替画像",
    en: "Stand-in for notes without a portrait",
  } as Localized,
  authorLicensed: {
    es: "Con licencia",
    ja: "ライセンス許諾あり",
    en: "Licensed",
  } as Localized,
  authorUnknown: {
    es: "Sin autoría registrada",
    ja: "作者の記録なし",
    en: "No author recorded",
  } as Localized,
};

/**
 * One language's version of a string, falling back to the site default.
 *
 * The fallback matters for a language added to `i18n.languages` before its
 * translations are written: the page then reads in the default language
 * rather than rendering the word `undefined`.
 */
export function pick(text: Localized, code: string): string {
  return text[code] ?? text[DEFAULT_LANGUAGE] ?? Object.values(text)[0] ?? "";
}

/** `fill(GALLERY.count, "es", { shown: 3, total: 49 })` -> `"3 de 49"`. */
export function fill(
  text: Localized,
  code: string,
  values: Record<string, string | number>,
): string {
  return Object.entries(values).reduce(
    (out, [key, value]) => out.replaceAll(`{${key}}`, String(value)),
    pick(text, code),
  );
}
