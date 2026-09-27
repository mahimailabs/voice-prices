import * as ToggleGroup from '@radix-ui/react-toggle-group';
import { animate } from 'animejs';
import { AudioLines, Brain, ChevronDown, Phone, RotateCcw, Search, SlidersHorizontal, TriangleAlert, Volume2 } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  ASSUMPTION_FIELDS,
  DEFAULT_ASSUMPTIONS,
  DEFAULT_REFS,
  perMinute,
  type Assumptions,
  type Option,
  type Picks,
  type Route,
} from '../lib/estimate';

// The hero: pick an STT, an LLM and a TTS (and optionally a phone line), and see what a call
// minute costs at listed rates, direct and through LiveKit Inference. Every number it shows is
// an estimate from the assumptions below it, and it says so beside the number.

type Opts = { stt: Option[]; llm: Option[]; tts: Option[]; phone: Option[] };

const money = (v: number) => {
  if (v === 0) return '$0';
  if (v >= 100) return `$${Math.round(v).toLocaleString('en-US')}`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${Number(v.toPrecision(3))}`;
};
const pct = (v: number) => (Math.abs(v) < 0.05 ? 'same' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
const PRESETS = [1_000, 10_000, 100_000, 1_000_000];
const PARTS = [
  { key: 'stt', label: 'STT', Icon: AudioLines },
  { key: 'llm', label: 'LLM', Icon: Brain },
  { key: 'tts', label: 'TTS', Icon: Volume2 },
  { key: 'phone', label: 'Phone', Icon: Phone },
] as const;

function Picker({
  label,
  Icon,
  options,
  value,
  onChange,
  describe,
  allowNone,
}: {
  label: string;
  Icon: typeof Brain;
  options: Option[];
  value: Option | null;
  onChange: (o: Option | null) => void;
  describe: (o: Option) => string;
  allowNone?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  const results = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = options.filter((o) => {
      const hay = `${o.provider} ${o.model} ${o.name}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
    return hits.slice(0, 60);
  }, [q, options]);
  const rows: (Option | null)[] = allowNone && !q ? [null, ...results] : results;

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const close = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  useEffect(() => setActive(0), [q]);

  const choose = (o: Option | null) => {
    onChange(o);
    setOpen(false);
    setQ('');
  };

  return (
    <div className="pick" ref={root}>
      <button
        type="button"
        className="pick__button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="pick__icon">
          <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
        </span>
        <span className="pick__text">
          <span className="pick__label">{label}</span>
          <span className="pick__value">
            {value ? (
              <>
                {value.provider} <span className="mono">{value.model}</span>
              </>
            ) : (
              'None'
            )}
          </span>
        </span>
        <span className="pick__rate mono">{value ? describe(value) : ''}</span>
        <ChevronDown size={15} strokeWidth={1.75} aria-hidden="true" className="pick__chev" />
      </button>
      {open && (
        <div className="pick__pop">
          <label className="pick__search">
            <Search size={14} strokeWidth={1.75} aria-hidden="true" />
            <span className="sr-only">Search {label}</span>
            <input
              ref={input}
              type="search"
              value={q}
              placeholder={`Search ${options.length.toLocaleString('en-US')} ${label} rates`}
              role="combobox"
              aria-controls={listId}
              aria-expanded="true"
              aria-activedescendant={rows[active] !== undefined ? `${listId}-${active}` : undefined}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setActive((i) => Math.min(i + 1, rows.length - 1));
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setActive((i) => Math.max(i - 1, 0));
                } else if (e.key === 'Enter') {
                  e.preventDefault();
                  if (rows[active] !== undefined) choose(rows[active]!);
                } else if (e.key === 'Escape') {
                  setOpen(false);
                }
              }}
            />
          </label>
          <ul className="pick__list" role="listbox" id={listId} aria-label={label}>
            {rows.length === 0 && <li className="pick__empty">No match</li>}
            {rows.map((o, i) => (
              <li
                key={o?.ref ?? 'none'}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={(o?.ref ?? null) === (value?.ref ?? null)}
                data-active={i === active || undefined}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(o);
                }}
              >
                {o ? (
                  <>
                    <span>
                      {o.provider} <span className="mono">{o.model}</span>
                    </span>
                    <span className="mono pick__opt-rate">{describe(o)}</span>
                  </>
                ) : (
                  <span>None</span>
                )}
              </li>
            ))}
            {!q && options.length > 60 && <li className="pick__empty">Type to search the rest</li>}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function Estimator({ options }: { options: Opts }) {
  const find = (list: Option[], ref: string | null) => (ref ? (list.find((o) => o.ref === ref) ?? list[0] ?? null) : null);
  const [picks, setPicks] = useState<Picks>({
    stt: find(options.stt, DEFAULT_REFS.stt),
    llm: find(options.llm, DEFAULT_REFS.llm),
    tts: find(options.tts, DEFAULT_REFS.tts),
    phone: find(options.phone, DEFAULT_REFS.phone),
  });
  const [minutes, setMinutes] = useState(10_000);
  const [a, setA] = useState<Assumptions>(DEFAULT_ASSUMPTIONS);
  const [route, setRoute] = useState<Route>('direct');

  const direct = perMinute(picks, a, 'direct');
  const build = perMinute(picks, a, 'build');
  const scale = perMinute(picks, a, 'scale');
  const shown = { direct, build, scale }[route];
  const onLiveKit = [picks.stt, picks.llm, picks.tts].some((o) => o?.livekit);
  const anyEstimated = [picks.stt, picks.llm, picks.tts, picks.phone].some((o) => o?.estimated);

  // The one moving part: the split bar eases to its new proportions whenever a pick changes.
  const bar = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  const widths = PARTS.map((p) => (shown.total > 0 ? (shown[p.key] / shown.total) * 100 : 0));
  useEffect(() => {
    const el = bar.current;
    if (!el) return;
    const segs = [...el.querySelectorAll<HTMLElement>('[data-seg]')];
    if (first.current || matchMedia('(prefers-reduced-motion: reduce)').matches) {
      first.current = false;
      segs.forEach((s, i) => (s.style.width = `${widths[i]}%`));
      return;
    }
    segs.forEach((s, i) => animate(s, { width: `${widths[i]}%`, duration: 700, ease: 'outExpo' }));
  }, [widths.join()]);

  const set = (k: keyof Picks) => (o: Option | null) => setPicks((p) => ({ ...p, [k]: o }));
  const per = {
    stt: (o: Option) => `$${Number(o.rates.per_min!.toPrecision(3))}/min`,
    llm: (o: Option) => `$${Number(o.rates.input_mtok!.toPrecision(3))} / $${Number(o.rates.output_mtok!.toPrecision(3))}`,
    tts: (o: Option) => `$${Number(o.rates.per_mchars!.toPrecision(3))}/1M`,
    phone: (o: Option) => `$${Number(o.rates.per_min!.toPrecision(3))}/min`,
  };

  return (
    <div className="est" id="estimate">
      <div className="est__head">
        <h2 className="est__title">Cost per call minute</h2>
        <span className="est__badge">
          <TriangleAlert size={12} strokeWidth={2} aria-hidden="true" /> Estimate
        </span>
      </div>

      <div className="est__picks">
        <Picker label="STT" Icon={AudioLines} options={options.stt} value={picks.stt} onChange={set('stt')} describe={per.stt} />
        <Picker label="LLM" Icon={Brain} options={options.llm} value={picks.llm} onChange={set('llm')} describe={per.llm} />
        <Picker label="TTS" Icon={Volume2} options={options.tts} value={picks.tts} onChange={set('tts')} describe={per.tts} />
        <Picker label="Phone line" Icon={Phone} options={options.phone} value={picks.phone} onChange={set('phone')} describe={per.phone} allowNone />
      </div>

      <div className="est__result" aria-live="polite">
        <div className="est__total">
          <span className="est__num mono">{money(shown.total)}</span>
          <span className="est__unit">per minute, estimated</span>
        </div>
        <div className="est__month">
          <span className="mono">{money(shown.total * minutes)}</span> for{' '}
          <label className="est__minutes">
            <span className="sr-only">Minutes per month</span>
            <input
              type="number"
              min={0}
              step={1000}
              value={minutes}
              onChange={(e) => setMinutes(Math.max(0, Number(e.target.value) || 0))}
              className="mono"
            />
          </label>{' '}
          minutes a month
        </div>
        <div className="est__presets" role="group" aria-label="Minutes per month">
          {PRESETS.map((m) => (
            <button key={m} type="button" className="chip" data-state={minutes === m ? 'on' : 'off'} onClick={() => setMinutes(m)}>
              {m >= 1_000_000 ? `${m / 1_000_000}M` : `${m / 1000}k`}
            </button>
          ))}
        </div>

        <div className="est__bar" ref={bar} role="img" aria-label={PARTS.map((p) => `${p.label} ${money(shown[p.key])}`).join(', ')}>
          {PARTS.map((p) => (
            <span key={p.key} data-seg data-part={p.key} />
          ))}
        </div>
        <ul className="est__legend">
          {PARTS.filter((p) => p.key !== 'phone' || picks.phone).map((p) => (
            <li key={p.key} data-part={p.key}>
              <span className="est__swatch" aria-hidden="true" />
              {p.label} <span className="mono">{money(shown[p.key])}</span>
            </li>
          ))}
        </ul>
      </div>

      {onLiveKit && (
        <div className="est__routes">
          <ToggleGroup.Root
            type="single"
            value={route}
            onValueChange={(v) => v && setRoute(v as Route)}
            aria-label="Buy direct or through LiveKit Inference"
            className="seg est__route-toggle"
          >
            <ToggleGroup.Item value="direct" className="seg__item">
              Direct
            </ToggleGroup.Item>
            <ToggleGroup.Item value="build" className="seg__item">
              LiveKit
            </ToggleGroup.Item>
            <ToggleGroup.Item value="scale" className="seg__item">
              LiveKit Scale
            </ToggleGroup.Item>
          </ToggleGroup.Root>
          <p className="est__route-note">
            {route === 'direct' ? (
              <>
                Through LiveKit Inference: <span className="mono">{money(build.total)}</span> ({pct(((build.total - direct.total) / (direct.total || 1)) * 100)}), on
                Scale <span className="mono">{money(scale.total)}</span> ({pct(((scale.total - direct.total) / (direct.total || 1)) * 100)}).
              </>
            ) : (
              <>
                {pct(((shown.total - direct.total) / (direct.total || 1)) * 100)} against buying each model direct.
              </>
            )}
            {route !== 'direct' && shown.missing.length > 0 && <> {shown.missing.join(' and ')} not sold on LiveKit, so priced direct.</>}
          </p>
        </div>
      )}

      <details className="est__assume">
        <summary>
          <SlidersHorizontal size={14} strokeWidth={1.75} aria-hidden="true" /> Assumptions
          <span className="est__assume-sum mono">
            {a.turns} turns, {a.inputTokens.toLocaleString('en-US')} in / {a.outputTokens} out tokens, {a.ttsChars} chars a minute
          </span>
        </summary>
        <div className="est__fields">
          {ASSUMPTION_FIELDS.map((f) => (
            <label key={f.key} className="est__field">
              <span>{f.label}</span>
              <input
                type="number"
                min={0}
                step={f.step}
                value={a[f.key]}
                className="mono"
                onChange={(e) => setA((prev) => ({ ...prev, [f.key]: Math.max(0, Number(e.target.value) || 0) }))}
              />
              <small>{f.hint}</small>
            </label>
          ))}
        </div>
        <p className="est__excluded">
          Not included: platform or session fees, hosting, free tiers, volume discounts, taxes, prompt caching, and any
          rate that depends on voice, region or plan.
        </p>
        <button type="button" className="est__reset" onClick={() => setA(DEFAULT_ASSUMPTIONS)}>
          <RotateCcw size={13} strokeWidth={1.75} aria-hidden="true" /> Reset assumptions
        </button>
      </details>

      <p className="est__warn" role="note">
        <TriangleAlert size={14} strokeWidth={1.75} aria-hidden="true" />
        <span>
          An estimate, not a quote. It multiplies catalog rates, which may be out of date or wrong, by the assumptions
          above; a real call can cost more or less.
          {anyEstimated && ' One of these picks is itself a vendor estimate, not a billed meter.'} Confirm every rate
          with the vendor before you budget against it.
        </span>
      </p>
    </div>
  );
}
