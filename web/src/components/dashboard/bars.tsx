import { appLabel, type CompetitorSov, type MetricsRow } from "@/lib/types";
import { pct, score } from "@/lib/format";

/** A thin horizontal bar with its value always printed (no hover needed). */
function Bar({ label, value, display, color, sub }: {
  label: string; value: number | null; display: string; color: string; sub?: string;
}) {
  const width = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className="grid grid-cols-[minmax(0,12rem)_1fr_3.5rem] items-center gap-3 text-sm" title={`${label}: ${display}`}>
      <div className="truncate">
        <div className="truncate">{label}</div>
        {sub && <div className="truncate text-xs text-muted">{sub}</div>}
      </div>
      <div className="h-2 rounded-full bg-surface-2">
        <div className="h-2 rounded-full" style={{ width: `${width}%`, background: color }} />
      </div>
      <div className="text-right tabular-nums">{display}</div>
    </div>
  );
}

export function ByAppBars({ rows }: { rows: MetricsRow[] }) {
  if (rows.length === 0) return <p className="text-sm text-ink-2">No answers in this period.</p>;
  return (
    <div className="space-y-3">
      {rows.map((r) => (
        <Bar key={r.grp} label={appLabel(r.grp)} value={r.visibility_score ?? r.mention_rate}
          display={r.visibility_score != null ? score(r.visibility_score) : pct(r.mention_rate)}
          sub={`Mentioned ${pct(r.mention_rate)} · ${r.answers} answers`} color="var(--series-1)" />
      ))}
      <p className="text-xs text-muted">
        Bar = visibility score{rows.some((r) => r.visibility_score == null) ? " (mention rate where not yet scored)" : ""}.
      </p>
    </div>
  );
}

/** You vs. competitors: you in the accent colour, competitors in neutral grey. */
export function CompetitorBars({ you, yourName, rows }: {
  you: number | null; yourName: string; rows: CompetitorSov[];
}) {
  const visible = rows.filter((r) => r.relationship !== "ignored");
  if (visible.length === 0 && you == null) {
    return <p className="text-sm text-ink-2">No competitor data in this period.</p>;
  }
  return (
    <div className="space-y-3">
      <Bar label={`${yourName} (you)`} value={you} display={pct(you)} color="var(--series-1)" />
      {visible.map((r) => (
        <Bar key={r.competitor_id} label={r.domain ?? r.name} value={r.share_of_voice}
          display={pct(r.share_of_voice)} sub={r.relationship === "indirect" ? "Indirect" : undefined}
          color="var(--series-muted)" />
      ))}
      <p className="text-xs text-muted">Bar = share of voice among you and your direct competitors.</p>
    </div>
  );
}
