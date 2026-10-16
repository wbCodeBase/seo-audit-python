"use client";

import { CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { pct, score, shortDate } from "@/lib/format";
import type { MetricsRow } from "@/lib/types";

// Three series on ONE 0–100 axis (all are 0–100 by definition). Slots 1–3 of
// the validated palette; identity is also carried by the legend and the
// table view below (light-mode slot 3 is under 3:1 contrast, so the table is
// required, not optional).
const SERIES = [
  { key: "visibility_score", name: "Visibility score", color: "var(--series-1)", fmt: score },
  { key: "mention_rate", name: "Mention rate %", color: "var(--series-2)", fmt: (v: number | null) => pct(v) },
  { key: "share_of_voice", name: "Share of voice %", color: "var(--series-3)", fmt: (v: number | null) => pct(v) },
] as const;

export function TrendChart({ rows }: { rows: MetricsRow[] }) {
  const data = rows.map((r) => ({ ...r, label: shortDate(r.grp) }));

  if (data.length === 0) {
    return <p className="py-12 text-center text-sm text-ink-2">No answers in this period yet.</p>;
  }

  return (
    <div>
      <LineChart
        responsive
        data={data}
        style={{ width: "100%", height: 260 }}
        margin={{ top: 8, right: 16, bottom: 0, left: -16 }}
      >
        <CartesianGrid stroke="var(--line)" vertical={false} />
        <XAxis dataKey="label" tick={{ fill: "var(--muted)", fontSize: 12 }} tickLine={false}
          axisLine={{ stroke: "var(--axis)" }} minTickGap={24} />
        <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tick={{ fill: "var(--muted)", fontSize: 12 }}
          tickLine={false} axisLine={false} />
        <Tooltip
          cursor={{ stroke: "var(--axis)" }}
          contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: "var(--ink)", fontWeight: 600 }}
          itemStyle={{ color: "var(--ink-2)" }}
          formatter={(value, name) => {
            const s = SERIES.find((x) => x.name === name);
            const v = typeof value === "number" ? value : null;
            return [s ? s.fmt(v) : String(value), name];
          }}
        />
        <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: "var(--ink-2)" }} />
        {SERIES.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2}
            dot={data.length <= 31 ? { r: 3, strokeWidth: 0, fill: s.color } : false}
            activeDot={{ r: 5, stroke: "var(--surface)", strokeWidth: 2 }}
            connectNulls isAnimationActive={false} />
        ))}
      </LineChart>

      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-ink-2">Show as table</summary>
        <div className="mt-2 max-h-64 overflow-auto">
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Day</th>
                {SERIES.map((s) => <th key={s.key} className="th text-right">{s.name}</th>)}
                <th className="th text-right">Answers</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.grp}>
                  <td className="td">{r.label}</td>
                  {SERIES.map((s) => <td key={s.key} className="td text-right tabular-nums">{s.fmt(r[s.key])}</td>)}
                  <td className="td text-right tabular-nums">{r.answers}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
