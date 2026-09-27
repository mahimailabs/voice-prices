// The home page estimate: what one minute of a cascaded voice agent (STT, LLM, TTS, and an
// optional phone line) costs at listed rates, under stated assumptions about a typical call.
// It is arithmetic on catalog rates, not a quote, and the page says so next to every number.
import { catalogOf, providersOf, type Category, type Model } from './catalog';

export type Rates = Record<string, number>;

export type Option = {
  ref: string; // provider/model, the catalog's own ids
  provider: string;
  model: string;
  name: string;
  rates: Rates;
  estimated: boolean;
  verified: string | null;
  /** The same model through LiveKit Inference, when LiveKit sells it. */
  livekit: { slug: string; build: Rates; scale: Rates | null } | null;
};

export type Assumptions = {
  sttShare: number; // billed STT minutes per call minute
  turns: number; // LLM requests per call minute
  inputTokens: number; // prompt tokens per request: system prompt, tools and history, resent every turn
  outputTokens: number; // completion tokens per request
  ttsChars: number; // characters synthesized per call minute
};

export const DEFAULT_ASSUMPTIONS: Assumptions = {
  sttShare: 1,
  turns: 4,
  inputTokens: 1500,
  outputTokens: 30,
  ttsChars: 450,
};

export const ASSUMPTION_FIELDS: { key: keyof Assumptions; label: string; hint: string; step: number }[] = [
  { key: 'sttShare', label: 'STT minutes per call minute', hint: 'Streaming STT bills the whole connected minute.', step: 0.1 },
  { key: 'turns', label: 'LLM turns per minute', hint: 'One request each time the caller finishes speaking.', step: 1 },
  { key: 'inputTokens', label: 'Input tokens per turn', hint: 'System prompt, tools and the history so far, sent again every turn.', step: 100 },
  { key: 'outputTokens', label: 'Output tokens per turn', hint: 'A short spoken reply, one or two sentences.', step: 5 },
  { key: 'ttsChars', label: 'TTS characters per minute', hint: 'The agent speaks about half the call, at about 150 words a minute.', step: 50 },
];

const RATE_KEYS: Record<'stt' | 'llm' | 'tts' | 'telephony', string[]> = {
  stt: ['per_min'],
  llm: ['input_mtok', 'output_mtok'],
  tts: ['per_mchars'],
  telephony: ['per_min'],
};

const pick = (values: Record<string, number>, keys: string[]): Rates | null => {
  const out: Rates = {};
  for (const k of keys) {
    if (typeof values[k] !== 'number') return null;
    out[k] = values[k]!;
  }
  return out;
};

function livekitIndex(category: Category) {
  const build = new Map<string, Model>();
  const scale = new Map<string, Model>();
  for (const p of providersOf(category)) {
    if (p.id === 'livekit') p.models.forEach((m) => build.set(m.id, m));
    if (p.id === 'livekit-scale') p.models.forEach((m) => scale.set(m.id, m));
  }
  // direct ref -> LiveKit slugs that are compared against it
  const byRef = new Map<string, string[]>();
  for (const row of catalogOf(category).comparison) {
    if (!row.direct_ref) continue;
    byRef.set(row.direct_ref, [...(byRef.get(row.direct_ref) ?? []), row.id]);
  }
  return { build, scale, byRef };
}

export function optionsFor(category: 'stt' | 'llm' | 'tts' | 'telephony'): Option[] {
  const keys = RATE_KEYS[category];
  const lk = category === 'telephony' ? null : livekitIndex(category);
  const out: Option[] = [];
  for (const p of providersOf(category)) {
    // Telephony is priced per carrier, LiveKit included; the rest are direct vendors only, and the
    // LiveKit price of the same model is carried alongside for the route comparison.
    if (category !== 'telephony' && p.group !== 'direct') continue;
    for (const m of p.models) {
      const rates = pick(m.values, keys);
      if (!rates) continue;
      const ref = `${p.id}/${m.id}`;
      let livekit: Option['livekit'] = null;
      if (lk) {
        const slugs = lk.byRef.get(ref) ?? [];
        // The slug that names this exact model wins over an alias of it (sonic-3 over sonic-3-latest).
        const slug = slugs.find((s) => s === ref) ?? slugs.find((s) => !s.includes('@')) ?? slugs[0];
        const b = slug ? lk.build.get(slug) : undefined;
        const bRates = b ? pick(b.values, keys) : null;
        if (slug && bRates) {
          const s = lk.scale.get(slug);
          livekit = { slug, build: bRates, scale: s ? pick(s.values, keys) : null };
        }
      }
      out.push({
        ref,
        provider: p.name,
        model: m.id,
        name: m.name,
        rates,
        estimated: Boolean(m.markers?.includes('estimated')),
        verified: m.verified ?? null,
        livekit,
      });
    }
  }
  return out;
}

export type Picks = { stt: Option | null; llm: Option | null; tts: Option | null; phone: Option | null };
export type Route = 'direct' | 'build' | 'scale';
export type Breakdown = { stt: number; llm: number; tts: number; phone: number; total: number; missing: string[] };

const ratesFor = (o: Option, route: Route): { rates: Rates; fallback: boolean } => {
  if (route === 'direct') return { rates: o.rates, fallback: false };
  if (!o.livekit) return { rates: o.rates, fallback: true };
  // Scale falls back to the Build/Ship rate where LiveKit does not discount the model.
  if (route === 'scale') return { rates: o.livekit.scale ?? o.livekit.build, fallback: false };
  return { rates: o.livekit.build, fallback: false };
};

/** Cost of one call minute. `missing` names the parts that are not on LiveKit and so are priced direct. */
export function perMinute(picks: Picks, a: Assumptions, route: Route = 'direct'): Breakdown {
  const missing: string[] = [];
  const part = (o: Option | null, label: string, f: (r: Rates) => number) => {
    if (!o) return 0;
    const { rates, fallback } = ratesFor(o, route);
    if (fallback) missing.push(label);
    return f(rates);
  };
  const stt = part(picks.stt, 'STT', (r) => (r.per_min ?? 0) * a.sttShare);
  const llm = part(
    picks.llm,
    'LLM',
    (r) => (a.turns * (a.inputTokens * (r.input_mtok ?? 0) + a.outputTokens * (r.output_mtok ?? 0))) / 1_000_000,
  );
  const tts = part(picks.tts, 'TTS', (r) => (a.ttsChars * (r.per_mchars ?? 0)) / 1_000_000);
  const phone = picks.phone ? (picks.phone.rates.per_min ?? 0) : 0;
  return { stt, llm, tts, phone, total: stt + llm + tts + phone, missing };
}

export const DEFAULT_REFS = {
  stt: 'deepgram/nova-3',
  llm: 'openai/gpt-4.1-mini',
  tts: 'cartesia/sonic-3',
  phone: null as string | null,
};
