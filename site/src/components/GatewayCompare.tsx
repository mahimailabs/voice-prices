import * as ToggleGroup from '@radix-ui/react-toggle-group';
import { useMemo, useState } from 'react';
import { fmtUsd, type ComparisonRow } from '../lib/catalog';

// LiveKit Inference against buying the same model direct, per category. The delta is Build/Ship
// against direct; Scale is shown beside it and falls back to Build/Ship where LiveKit does not
// discount the model.

export type CompareSet = { key: string; label: string; unit: string; rows: ComparisonRow[] };

const delta = (v: number | null) => {
  if (v === null) return { text: 'LiveKit only', tone: 'muted' };
  if (Math.abs(v) < 0.05) return { text: 'same', tone: 'same' };
  return { text: `${v > 0 ? '+' : ''}${v.toFixed(1)}%`, tone: v > 0 ? 'up' : 'down' };
};

export default function GatewayCompare({ sets, withOnlyLiveKit = false }: { sets: CompareSet[]; withOnlyLiveKit?: boolean }) {
  const [key, setKey] = useState(sets[0]?.key ?? '');
  const set = sets.find((s) => s.key === key) ?? sets[0];
  const rows = useMemo(() => {
    if (!set) return [];
    const list = set.rows.filter((r) => withOnlyLiveKit || r.direct !== null);
    return [...list].sort((a, b) => (b.delta ?? -Infinity) - (a.delta ?? -Infinity) || a.id.localeCompare(b.id));
  }, [set, withOnlyLiveKit]);
  if (!set) return null;
  const counts = {
    above: rows.filter((r) => (r.delta ?? 0) >= 0.05).length,
    same: rows.filter((r) => r.delta !== null && Math.abs(r.delta) < 0.05).length,
    below: rows.filter((r) => r.delta !== null && r.delta <= -0.05).length,
  };

  return (
    <div className="gw">
      {sets.length > 1 && (
        <ToggleGroup.Root type="single" value={key} onValueChange={(v) => v && setKey(v)} aria-label="Category" className="seg gw__tabs">
          {sets.map((s) => (
            <ToggleGroup.Item key={s.key} value={s.key} className="seg__item">
              {s.label}
            </ToggleGroup.Item>
          ))}
        </ToggleGroup.Root>
      )}
      <p className="gw__summary">
        <span className="mono">{counts.above}</span> above direct, <span className="mono">{counts.same}</span> the same,{' '}
        <span className="mono">{counts.below}</span> below, out of <span className="mono">{rows.length}</span> models with both rates.
        Rates in {set.unit}.
      </p>
      <div className="mt__scroll">
        <table className="mt__table gw__table">
          <thead>
            <tr>
              <th>LiveKit model</th>
              <th className="num">Direct</th>
              <th className="num">LiveKit</th>
              <th className="num">Scale</th>
              <th className="num">vs direct</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const d = delta(r.delta);
              return (
                <tr key={r.id}>
                  <td className="mt__model">
                    <span className="mt__id mono">{r.id}</span>
                  </td>
                  <td className="num mono">{fmtUsd(r.direct)}</td>
                  <td className="num mono">{fmtUsd(r.livekit)}</td>
                  <td className="num mono">{r.scale === null ? <span className="gw__fallback">{fmtUsd(r.livekit)}</span> : fmtUsd(r.scale)}</td>
                  <td className="num">
                    <span className="delta mono" data-tone={d.tone}>
                      {d.text}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
