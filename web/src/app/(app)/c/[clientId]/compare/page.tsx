import type { Metadata } from "next";
import Link from "next/link";
import { requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { parseFilters } from "@/lib/filters";
import { compare, type Cell, type CompareAnswer, type Verdict } from "@/lib/compare";
import { appLabel } from "@/lib/types";
import { pct } from "@/lib/format";

export const metadata: Metadata = { title: "API vs real" };

const VERDICT: Record<Verdict, { label: string; cls: string }> = {
  agree: { label: "Agrees", cls: "bg-good-wash text-good-ink" },
  api_over: { label: "API over-reports", cls: "bg-warn-wash text-ink" },
  api_under: { label: "API misses you", cls: "bg-critical-wash text-critical-ink" },
};

function CellView({ cell, clientId }: { cell?: Cell; clientId: number }) {
  if (!cell) return <span className="text-muted">—</span>;
  if (cell.noOverview) {
    return (
      <Link href={`/c/${clientId}/answers?answer=${cell.answerId}`} className="text-xs text-muted hover:underline"
        title="Google showed no AI answer for this search">
        No AI answer shown
      </Link>
    );
  }
  return (
    <Link href={`/c/${clientId}/answers?answer=${cell.answerId}`}
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ${
        cell.named ? "bg-good-wash text-good-ink" : "bg-critical-wash text-critical-ink"}`}>
      <span aria-hidden>{cell.named ? "✓" : "✕"}</span>
      {cell.named ? `Named${cell.position ? ` #${cell.position}` : ""}` : "Missing"}
      {cell.cited && <span className="font-normal">· cited</span>}
    </Link>
  );
}

export default async function ComparePage(props: PageProps<"/c/[clientId]/compare">) {
  const { clientId } = await props.params;
  const { client } = await requireClient(clientId);
  const filters = parseFilters(await props.searchParams);
  const supabase = await createClient();

  const [{ data, error }, { data: questions }] = await Promise.all([
    supabase.from("answers")
      .select("id, question_id, ai_app, created_at, usage, " +
        "mentions(entity_type, mentioned, mention_position, cited), citations(domain)")
      .eq("client_id", client.id).is("error", null)
      .gte("created_at", `${filters.from}T00:00:00+05:30`)
      .order("created_at", { ascending: false })
      .limit(3000),
    supabase.from("questions").select("id, text").eq("client_id", client.id),
  ]);
  const answers = (data ?? []) as unknown as CompareAnswer[];
  const { rows, stats, realApps, apiApps } = compare(answers);
  const qText = new Map((questions ?? []).map((q) => [q.id as number, q.text as string]));
  const rangeLinks = ["7", "30", "90"];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-3xl text-sm text-ink-2">
          The same questions, answered two ways: <strong>real user view</strong> (what people see in ChatGPT, Gemini and
          Google) and the <strong>API estimate</strong> (the vendors&apos; developer APIs). Use this to decide how far to
          trust the cheaper API numbers.
        </p>
        <nav className="flex gap-1 text-sm" aria-label="Period">
          {rangeLinks.map((r) => (
            <Link key={r} href={`?range=${r}`} aria-current={filters.range === r ? "page" : undefined}
              className={`chip ${filters.range === r ? "border-ink bg-surface-2 text-ink" : ""}`}>
              {r} days
            </Link>
          ))}
        </nav>
      </div>

      {error && <p className="rounded-lg bg-critical-wash px-3 py-2 text-sm text-critical-ink">{error.message}</p>}

      {realApps.length === 0 ? (
        <section className="card p-4 text-sm text-ink-2">
          No real-user answers in this period yet. An admin can turn on the real-user engines (ChatGPT · real, Google
          AI Overviews, …) in this client&apos;s Settings; they appear here after the next run.
        </section>
      ) : stats.length === 0 ? (
        <section className="card p-4 text-sm text-ink-2">
          Real-user answers are coming in, but none of them has an API counterpart to compare with (only ChatGPT has
          both today). The table below still shows every engine side by side.
        </section>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {stats.map((s) => (
            <section key={s.api} className="card p-4">
              <h2 className="text-sm text-ink-2">{appLabel(s.api)} vs {appLabel(s.real)}</h2>
              <div className="mt-1 text-3xl font-semibold tracking-tight">{pct(s.compared ? (100 * s.agree) / s.compared : null)}</div>
              <p className="text-xs text-muted">agreement on whether you&apos;re named · {s.compared} questions</p>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                <dt className="text-ink-2">API over-reports</dt><dd className="text-right tabular-nums">{s.apiOver}</dd>
                <dt className="text-ink-2">API misses you</dt><dd className="text-right tabular-nums">{s.apiUnder}</dd>
                <dt className="text-ink-2">Same sources cited</dt><dd className="text-right tabular-nums">{pct(s.overlap)}</dd>
                <dt className="text-ink-2">Position gap</dt>
                <dd className="text-right tabular-nums">{s.positionGap == null ? "—" : `${s.positionGap.toFixed(1)} places`}</dd>
              </dl>
            </section>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <section className="card overflow-x-auto">
          <table className="w-full min-w-[720px]">
            <thead>
              <tr>
                <th className="th">Question</th>
                {realApps.map((a) => <th key={a} className="th">{appLabel(a)}</th>)}
                {apiApps.map((a) => <th key={a} className="th text-ink-2">{appLabel(a)}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.questionId}>
                  <td className="td"><div className="line-clamp-2">{qText.get(r.questionId) ?? `#${r.questionId}`}</div></td>
                  {realApps.map((a) => <td key={a} className="td"><CellView cell={r.cells[a]} clientId={client.id} /></td>)}
                  {apiApps.map((a) => {
                    const v = r.verdicts[a];
                    return (
                      <td key={a} className="td">
                        <div className="flex flex-col items-start gap-1">
                          <CellView cell={r.cells[a]} clientId={client.id} />
                          {v && v !== "agree" && <span className={`rounded px-1.5 text-[11px] ${VERDICT[v].cls}`}>{VERDICT[v].label}</span>}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-4 pb-3 text-xs text-muted">Latest answer per question and engine in the period. Click a cell to read it.</p>
        </section>
      )}
    </div>
  );
}
