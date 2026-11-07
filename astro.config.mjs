// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

// https://astro.build/config
export default defineConfig({
  // The deployed origin. Astro needs it to turn a page's path into an absolute
  // URL, which is what `<link rel="canonical">` and the sitemap are made of --
  // neither can be written relatively. Until this was set, `Astro.site` was
  // undefined and both were simply impossible.
  //
  // It also fixes what a reader's address bar will say, which matters more here
  // than on most sites: every language of a note lives at *one* URL and is
  // switched client-side, so the URL is the whole address of a note and the
  // only thing a reader can link to.
  site: 'https://nia-tero-exxet.pages.dev',

  // One entry per generated page. Because the languages share a URL there are
  // no locale paths to declare and no `hreflang` alternates to emit: the
  // sitemap's `i18n` option describes a site with `/es/...` and `/ja/...`
  // trees, which this deliberately is not.
  integrations: [sitemap()],
});
