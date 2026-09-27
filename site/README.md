# prices.mahimai.ca

The Voice Prices site: Astro with React islands, served as static files from Cloudflare Workers.

Every number comes from `src/data/catalog.json`, which `make build` (or `make build-site`) writes
from `prices/data.json` through the same catalog builder the Mintlify pages use. Never edit it by
hand; change the provider YAML and rebuild.

```bash
npm ci
npm run dev      # http://localhost:4321
npm run build    # writes dist/
```

Every page that shows a rate carries the reference-only warning (`src/components/Caveat.astro`),
and the cost-per-minute calculator labels its result an estimate. `tests/test_site_data.py` checks both.
