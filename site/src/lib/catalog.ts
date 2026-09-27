// The site's one data source: src/data/catalog.json, written by `make build-site` from
// prices/data.json through the same catalog builder the Mintlify pages use.
import raw from '../data/catalog.json';

export type Category = 'stt' | 'llm' | 'tts' | 's2s' | 'vad' | 'agent' | 'telephony';
export type Status = 'verified' | 'imported' | 'seed';

export type Model = {
  id: string;
  name: string;
  values: Record<string, number>;
  markers?: string[];
  status?: Status;
  verified?: string;
  source?: string;
  comments?: string;
};

export type Provider = {
  id: string;
  slug: string;
  name: string;
  group: 'direct' | 'gateway' | 'huggingface';
  pricingTier: string | null;
  pricingUrls: string[];
  yaml: string | null;
  stalenessDays: number;
  models: Model[];
};

export type Column = { header: string; key: string; kind: 'usd' | 'int' };

export type ComparisonRow = {
  id: string;
  name: string;
  direct: number | null;
  livekit: number | null;
  scale: number | null;
  delta: number | null;
  direct_ref: string | null;
};

type Data = {
  repo: string;
  gatewaySummary: { compared: number; atOrBelow: number; scaleBelow: number; llmCompared: number; llmIdentical: number };
  removalForm: string;
  addProvider: string;
  categories: Record<Category, { columns: Column[]; tokenColumns: Column[]; providers: Provider[]; comparison: ComparisonRow[] }>;
};

export const data = raw as unknown as Data;
export const REPO = data.repo;
/** The same direct-vs-LiveKit counts the README prints, computed once in Python. */
export const GATEWAY = data.gatewaySummary;
export const REMOVAL_FORM = data.removalForm;
export const ADD_PROVIDER = data.addProvider;
export const PYPI = 'https://pypi.org/project/voice-prices/';

export const CATEGORIES: Category[] = ['stt', 'llm', 'tts', 's2s', 'vad', 'agent', 'telephony'];

export type CategoryMeta = {
  tab: string;
  title: string;
  /** The unit the headline rate is shown in. */
  unit: string;
  blurb: string;
  /** How the rate was converted from what the YAML stores. */
  unitNote: string;
  note?: string;
};

export const META: Record<Category, CategoryMeta> = {
  stt: {
    tab: 'STT',
    title: 'Speech-to-text',
    unit: '$ / min',
    blurb: 'Streaming and batch transcription, per minute of audio.',
    unitNote:
      'Stored as US dollars per 1,000 seconds of input audio and shown per minute. Vendors quote per minute, per second or per hour, so the figure here is a conversion, not a quote.',
    note: 'Streaming and batch are separate rows, as are language tiers, because vendors price them separately.',
  },
  llm: {
    tab: 'LLM',
    title: 'LLM',
    unit: '$ / 1M tokens',
    blurb: 'Input, output and cached input, per million tokens.',
    unitNote: 'Stored and shown as US dollars per 1,000,000 tokens.',
    note: 'LLM rows carry no per-model verification status. They are cross-checked against external aggregators rather than against one vendor page each.',
  },
  tts: {
    tab: 'TTS',
    title: 'Text-to-speech',
    unit: '$ / 1M chars',
    blurb: 'Speech synthesis, per million characters of input text.',
    unitNote:
      'Stored as US dollars per 1,000 characters and shown per 1,000,000. Vendors that sell credits are converted at a named plan tier, recorded in the notes on each row.',
  },
  s2s: {
    tab: 'S2S',
    title: 'Speech-to-speech',
    unit: '$ / 1M tokens',
    blurb: 'Realtime models that bill audio as tokens in both directions.',
    unitNote: 'All rates are US dollars per 1,000,000 tokens.',
    note: 'A model is listed here only when it prices audio in and audio out. A chat model that merely accepts audio stays under LLM.',
  },
  vad: {
    tab: 'VAD',
    title: 'Voice activity detection',
    unit: '$ / min',
    blurb: 'Hosted or licensed VAD with a published rate.',
    unitNote: 'Stored as US dollars per 1,000 seconds of processed audio and shown per minute.',
    note: 'Most VAD (Silero, WebRTC, the LiveKit turn detector) runs locally and costs CPU, not dollars, so it has no rows here.',
  },
  agent: {
    tab: 'Agents',
    title: 'Voice agent platforms',
    unit: '$ / min',
    blurb: 'Bundled platforms that sell one blended minute.',
    unitNote: 'Stored as US dollars per 1,000 minutes of agent session time and shown per minute.',
    note: 'These rates do not compare with the rest of the catalog. A bundled minute covers whatever the platform chose to put in it, and several exclude the model or telephony. Read the notes on each row.',
  },
  telephony: {
    tab: 'Telephony',
    title: 'Telephony',
    unit: '$ / min',
    blurb: 'The carrier leg: putting the call on the phone network.',
    unitNote: 'Stored as US dollars per 1,000 connected call minutes and shown per minute.',
    note: 'A line you pay on top, not instead. Direction, number type and country are in the model id because each changes the rate.',
  },
};

export const catalogOf = (c: Category) => data.categories[c];
export const providersOf = (c: Category) => data.categories[c].providers;
export const modelCount = (c: Category) => providersOf(c).reduce((n, p) => n + p.models.length, 0);

export const totals = (() => {
  const providers = new Set<string>();
  let models = 0;
  let verified = 0;
  let voice = 0;
  let api = 0;
  for (const c of CATEGORIES) {
    for (const p of providersOf(c)) {
      providers.add(p.id);
      models += p.models.length;
      for (const m of p.models) {
        if (m.markers?.includes('api')) api++;
        if (c === 'llm') continue;
        voice++;
        if (m.status === 'verified') verified++;
      }
    }
  }
  return { providers: providers.size, models, verified, voice, api };
})();

/** The headline rate a category sorts and compares on. */
export const primaryKey: Record<Category, string> = {
  stt: 'per_min',
  llm: 'input_mtok',
  tts: 'per_mchars',
  s2s: 'input_audio_mtok',
  vad: 'per_min',
  agent: 'per_min',
  telephony: 'per_min',
};

/** Rates span $0.0000042 to $600 per unit. Four significant figures hides float noise
 * (0.00250002 becomes 0.0025) without rounding a real rate away. */
export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return '-';
  if (v === 0) return '$0';
  const s = Number(v.toPrecision(4)).toString();
  return `$${s.includes('e') ? Number(v.toPrecision(4)).toFixed(10).replace(/0+$/, '') : s}`;
}

export const fmtInt = (v: number | null | undefined) => (v === null || v === undefined ? '-' : Math.round(v).toLocaleString('en-US'));

export function range(c: Category): { min: number; max: number; n: number } | null {
  const key = primaryKey[c];
  const rates = providersOf(c)
    .filter((p) => p.group !== 'huggingface')
    .flatMap((p) => p.models.map((m) => m.values[key]))
    .filter((v): v is number => typeof v === 'number' && v > 0);
  if (!rates.length) return null;
  return { min: Math.min(...rates), max: Math.max(...rates), n: rates.length };
}

export const categoryHref = (c: Category) => `/${c}/`;
export const providerHref = (c: Category, p: Provider) => `/${c}/${p.slug}/`;

export const GROUP_LABEL: Record<Provider['group'], string> = {
  direct: 'Direct vendors',
  gateway: 'Gateways',
  huggingface: 'HuggingFace Inference',
};

export const MARKER_NOTES: Record<string, string> = {
  api: 'The vendor publishes this rate at a machine-readable endpoint, so it is re-read and compared automatically.',
  tiered: 'The rate changes above a usage threshold. The base rate is shown.',
  daily: 'The rate changes with the time of day. The standard rate is shown.',
  scheduled: 'This price has dated changes. The regular rate is shown; promotion dates are in the provider YAML.',
  voices: 'Some voice classes cost a multiple of the base rate.',
  estimated:
    'Not the meter the vendor bills on, so it will not match an invoice. Either the vendor restates a bill charged in another unit, or it publishes only a floor or a range.',
};

/** Whole days between a YYYY-MM-DD date and today, in UTC. */
export function ageDays(date: string, today = new Date()): number {
  const then = Date.parse(`${date}T00:00:00Z`);
  const now = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Math.floor((now - then) / 86_400_000);
}

export const domainOf = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
};

export type TableRow = Model & {
  provider: { id: string; name: string; href: string; group: Provider['group']; stalenessDays: number };
};

/** Every row of a category, or of one provider in it, tagged with its provider. */
export function rowsFor(c: Category, only?: Provider): TableRow[] {
  return providersOf(c)
    .filter((p) => !only || p.id === only.id)
    .flatMap((p) =>
      p.models.map((m) => ({
        ...m,
        provider: { id: p.id, name: p.name, href: providerHref(c, p), group: p.group, stalenessDays: p.stalenessDays },
      })),
    );
}

/** The category's columns that carry a value on at least one of these rows. Token-priced voice
 * columns join only when some row has no rate in the category's own unit. */
export function columnsFor(c: Category, rows: Model[]): Column[] {
  const cat = catalogOf(c);
  const primary = cat.columns[0]?.key;
  const tokenOnly = primary !== undefined && rows.some((r) => r.values[primary] === undefined);
  const cols = [...cat.columns, ...(tokenOnly ? cat.tokenColumns.filter((t) => !cat.columns.some((x) => x.key === t.key)) : [])];
  return cols.filter((col) => rows.some((r) => r.values[col.key] !== undefined));
}
