import * as ToggleGroup from '@radix-ui/react-toggle-group';
import { ArrowDown, ArrowUp, ArrowUpRight, Search } from 'lucide-react';
import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { MARKER_NOTES, ageDays, fmtInt, fmtUsd, type Column, type Model, type Provider } from '../lib/catalog';

// One category's models, or one provider's: searchable, sortable, filterable by provider group.
// Staleness is worked out here, against the reader's date, because a stale badge baked into a
// static page would start lying the day after it was built.

export type Row = Model & {
  provider: { id: string; name: string; href: string; group: Provider['group']; stalenessDays: number };
};

const PAGE = 50;
const GROUPS: { value: Provider['group'] | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'direct', label: 'Direct vendors' },
  { value: 'gateway', label: 'Gateways' },
  { value: 'huggingface', label: 'HuggingFace' },
];

type SortKey = { key: string; dir: 1 | -1 };

function freshness(m: Row, today: Date | null): { label: string; tone: string; title: string } | null {
  if (!m.status) return null;
  if (m.status !== 'verified') {
    return {
      label: m.status,
      tone: 'muted',
      title: m.status === 'imported' ? 'Never confirmed by a human. Imported from another catalog.' : 'Never confirmed by a human. Bootstrap data.',
    };
  }
  if (!m.verified || !today) return { label: 'verified', tone: 'ok', title: `Confirmed by a human on ${m.verified}` };
  const age = ageDays(m.verified, today);
  return age > m.provider.stalenessDays
    ? { label: 'stale', tone: 'warn', title: `Confirmed ${age} days ago, past this provider's ${m.provider.stalenessDays}-day threshold. It may have moved.` }
    : { label: 'verified', tone: 'ok', title: `Confirmed by a human ${age} days ago` };
}

export default function ModelTable({
  rows,
  columns,
  showProvider = true,
  showStatus = true,
  showComments = false,
  defaultSort,
}: {
  rows: Row[];
  columns: Column[];
  showProvider?: boolean;
  showStatus?: boolean;
  showComments?: boolean;
  defaultSort?: string;
}) {
  const [q, setQ] = useState('');
  const [group, setGroup] = useState<string>('all');
  const [sort, setSort] = useState<SortKey>({ key: defaultSort ?? 'model', dir: 1 });
  const [limit, setLimit] = useState(PAGE);
  const [today, setToday] = useState<Date | null>(null);
  useEffect(() => setToday(new Date()), []);
  const query = useDeferredValue(q.trim().toLowerCase());

  const groups = GROUPS.filter((g) => g.value === 'all' || rows.some((r) => r.provider.group === g.value));

  const results = useMemo(() => {
    const words = query.split(/\s+/).filter(Boolean);
    const hits = rows.filter((r) => {
      if (group !== 'all' && r.provider.group !== group) return false;
      if (!words.length) return true;
      const hay = `${r.provider.name} ${r.provider.id} ${r.id} ${r.name}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
    const val = (r: Row): string | number | null =>
      sort.key === 'model' ? `${r.provider.name} ${r.id}` : sort.key === 'verified' ? (r.verified ?? null) : (r.values[sort.key] ?? null);
    return [...hits].sort((x, y) => {
      const a = val(x);
      const b = val(y);
      // Rows without the value always sink, whichever way the column is sorted.
      if (a === null && b === null) return 0;
      if (a === null) return 1;
      if (b === null) return -1;
      return (a < b ? -1 : a > b ? 1 : 0) * sort.dir;
    });
  }, [rows, query, group, sort]);

  const shown = results.slice(0, limit);
  const toggleSort = (key: string) => setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: 1 }));
  const SortIcon = ({ k }: { k: string }) =>
    sort.key === k ? (
      sort.dir === 1 ? (
        <ArrowUp size={12} strokeWidth={2} aria-hidden="true" />
      ) : (
        <ArrowDown size={12} strokeWidth={2} aria-hidden="true" />
      )
    ) : null;
  const ariaSort = (k: string) => (sort.key === k ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined);
  const markers = [...new Set(shown.flatMap((r) => r.markers ?? []))];

  return (
    <div className="mt">
      <div className="mt__bar">
        <label className="finder__search">
          <Search size={16} strokeWidth={1.75} aria-hidden="true" />
          <span className="sr-only">Search models</span>
          <input
            type="search"
            value={q}
            placeholder={showProvider ? 'Search by provider or model' : 'Search models'}
            onChange={(e) => {
              setQ(e.target.value);
              setLimit(PAGE);
            }}
          />
        </label>
        {showProvider && groups.length > 2 && (
          <ToggleGroup.Root
            type="single"
            value={group}
            onValueChange={(v) => {
              if (!v) return;
              setGroup(v);
              setLimit(PAGE);
            }}
            aria-label="Provider group"
            className="mt__groups"
          >
            {groups.map((g) => (
              <ToggleGroup.Item key={g.value} value={g.value} className="chip">
                {g.label}
              </ToggleGroup.Item>
            ))}
          </ToggleGroup.Root>
        )}
      </div>
      <p className="finder__count mono" aria-live="polite">
        {results.length === rows.length ? `${rows.length.toLocaleString('en-US')} models` : `${results.length.toLocaleString('en-US')} of ${rows.length.toLocaleString('en-US')} models`}
      </p>
      <div className="mt__scroll">
        <table className="mt__table">
          <thead>
            <tr>
              <th aria-sort={ariaSort('model')}>
                <button type="button" onClick={() => toggleSort('model')}>
                  Model <SortIcon k="model" />
                </button>
              </th>
              {columns.map((c) => (
                <th key={c.key} className="num" aria-sort={ariaSort(c.key)}>
                  <button type="button" onClick={() => toggleSort(c.key)}>
                    {c.header} <SortIcon k={c.key} />
                  </button>
                </th>
              ))}
              {showStatus && (
                <th aria-sort={ariaSort('verified')}>
                  <button type="button" onClick={() => toggleSort('verified')}>
                    Checked <SortIcon k="verified" />
                  </button>
                </th>
              )}
              <th className="mt__src">Source</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const f = showStatus ? freshness(r, today) : null;
              return (
                <tr key={`${r.provider.id}/${r.id}`}>
                  <td className="mt__model">
                    {showProvider && (
                      <a className="mt__provider" href={r.provider.href}>
                        {r.provider.name}
                      </a>
                    )}
                    <span className="mt__id mono">{r.id}</span>
                    {r.markers && r.markers.length > 0 && (
                      <span className="mt__markers">
                        {r.markers.map((m) => (
                          <span key={m} className="tag" data-marker={m} title={MARKER_NOTES[m]}>
                            {m}
                          </span>
                        ))}
                      </span>
                    )}
                    {showComments && r.comments && <span className="mt__comment">{r.comments}</span>}
                  </td>
                  {columns.map((c) => (
                    <td key={c.key} className="num mono">
                      {c.kind === 'usd' ? fmtUsd(r.values[c.key]) : fmtInt(r.values[c.key])}
                    </td>
                  ))}
                  {showStatus && (
                    <td className="mt__status">
                      {f ? (
                        <span className="status" data-tone={f.tone} title={f.title}>
                          {f.label}
                        </span>
                      ) : (
                        '-'
                      )}
                      {r.verified && <span className="mono mt__date">{r.verified}</span>}
                    </td>
                  )}
                  <td className="mt__src">
                    {r.source ? (
                      <a href={r.source} rel="noopener" target="_blank" data-source={r.provider.id} aria-label={`${r.provider.name} pricing page`}>
                        <ArrowUpRight size={15} strokeWidth={1.75} aria-hidden="true" />
                      </a>
                    ) : (
                      <span aria-hidden="true">-</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {results.length === 0 && <p className="finder__empty">No model matches that search.</p>}
      {shown.length < results.length && (
        <button type="button" className="finder__more" onClick={() => setLimit((l) => l + PAGE * 2)}>
          Show more <span className="mono">({(results.length - shown.length).toLocaleString('en-US')})</span>
        </button>
      )}
      {markers.length > 0 && (
        <dl className="mt__notes">
          {markers.map((m) => (
            <div key={m}>
              <dt className="tag" data-marker={m}>
                {m}
              </dt>
              <dd>{MARKER_NOTES[m]}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
