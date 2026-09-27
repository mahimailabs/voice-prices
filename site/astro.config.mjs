// @ts-check
// prices.mahimai.ca: a static site rendered from src/data/catalog.json, which `make build-site`
// writes from prices/data.json. The numbers here are the numbers the Python package ships.
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

export default defineConfig({
  site: 'https://prices.mahimai.ca',
  trailingSlash: 'always',
  // Keep source whitespace: compression drops the space between a line of text and a link that
  // starts the next line ("or\n<a>" rendered as "or<a>").
  compressHTML: false,
  integrations: [react()],
});
