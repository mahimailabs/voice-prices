// @ts-check
// prices.mahimai.ca: a static site rendered from src/data/catalog.json, which `make build-site`
// writes from prices/data.json. The numbers here are the numbers the Python package ships.
// The long-form pages (how-fresh, contribute, pricing-feed, livekit-coverage) render the MDX
// files in ../docs directly, so their text has one source.
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import react from '@astrojs/react';
import { fileURLToPath } from 'node:url';

/** Keep a fence's title (```yaml TTS) as data-title so a CodeGroup can label each block. */
const fenceTitle = {
  name: 'fence-title',
  /** @param {any} node */
  pre(node) {
    const raw = this.options.meta?.__raw?.trim();
    if (raw) node.properties['data-title'] = raw;
  },
};

export default defineConfig({
  site: 'https://prices.mahimai.ca',
  trailingSlash: 'always',
  // Keep source whitespace: compression drops the space between a line of text and a link that
  // starts the next line ("or\n<a>" rendered as "or<a>").
  compressHTML: false,
  integrations: [react(), mdx()],
  markdown: {
    shikiConfig: {
      themes: { light: 'github-light', dark: 'github-dark' },
      defaultColor: false,
      transformers: [fenceTitle],
    },
  },
  vite: {
    // The MDX pages live in ../docs, outside the site root.
    server: { fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] } },
  },
});
