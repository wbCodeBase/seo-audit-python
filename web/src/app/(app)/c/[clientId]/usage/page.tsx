import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { todayIST } from "@/lib/filters";
import { appLabel, SOURCE_LABEL, sourceOf, type SpendRow } from "@/lib/types";
import { usd } from "@/lib/format";

export const metadata: Metadata = { title: "Usage" };

export default async function UsagePage(props: PageProps<"/c/[clientId]/usage">) {
  const { clientId } = await props.params;
  const { client, manageable } = await requireClient(clientId);
  if (!manageable) redirect(`/c/${client.id}`);
  const supabase = await createClient();

  const today = todayIST();
  const monthStart = today.slice(0, 8) + "01";
  const [{ data: spendData, error }, { data: full }] = await Promise.all([
    supabase.rpc("geo_spend", { p_from: monthStart, p_to: today, p_client: client.id }),
    supabase.from("clients").select("monthly_budget_usd, engines").eq("id", client.id).maybeSingle(),
  ]);
  const rows = ((spendData ?? []) as SpendRow[]).sort((a, b) => b.cost_usd - a.cost_usd);
  const spent = rows.reduce((s, r) => s + r.cost_usd, 0);
  const answers = rows.reduce((s, r) => s + r.answers, 0);
  const budget = (full?.monthly_budget_usd as number | null) ?? null;

  const day = Number(today.slice(8, 10));
  const [y, m] = today.split("-").map(Number);
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const projected = day ? (spent / day) * daysInMonth : spent;
  const used = budget ? Math.min(100, (100 * spent) / budget) : null;

  return (
    <div className="space-y-4">
      {error && <p className="rounded-lg bg-critical-wash px-3 py-2 text-sm text-critical-ink">{error.message}</p>}
      <div className="grid gap-3 md:grid-cols-3">
        <section className="card p-4">
          <div className="text-sm text-ink-2">Spent this month</div>
          <div className="mt-1 text-3xl font-semibold tracking-tight">{usd(spent)}</div>
          <div className="text-xs text-muted">{answers} answers · {usd(answers ? spent / answers : null, 4)} each</div>
        </section>
        <section className="card p-4">
          <div className="text-sm text-ink-2">Monthly budget</div>
          <div className="mt-1 text-3xl font-semibold tracking-tight">{budget == null ? "No cap" : usd(budget)}</div>
          {used != null && (
            <div className="mt-2">
              <div className="h-2 overflow-hidden rounded bg-surface-2" role="meter" aria-valuenow={Math.round(used)}
                aria-valuemin={0} aria-valuemax={100} aria-label="Budget used">
                <div className={`h-full ${used >= 90 ? "bg-critical-ink" : "bg-accent"}`} style={{ width: `${used}%` }} />
              </div>
              <div className="mt-1 text-xs text-muted">{Math.round(used)}% used — runs stop before going over</div>
            </div>
          )}
        </section>
        <section className="card p-4">
          <div className="text-sm text-ink-2">Projected month end</div>
          <div className="mt-1 text-3xl font-semibold tracking-tight">{usd(projected)}</div>
          <div className="text-xs text-muted">at this month&apos;s pace so far</div>
        </section>
      </div>

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 font-medium">By engine</h2>
        <table className="mt-2 w-full min-w-[560px]">
          <thead>
            <tr>
              <th className="th">Engine</th><th className="th">Kind</th>
              <th className="th text-right">Answers</th><th className="th text-right">Cost</th>
              <th className="th text-right">Per answer</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ai_app}>
                <td className="td">{appLabel(r.ai_app)}</td>
                <td className="td text-ink-2">{SOURCE_LABEL[sourceOf(r.ai_app)]}</td>
                <td className="td text-right tabular-nums">{r.answers}</td>
                <td className="td text-right tabular-nums">{usd(r.cost_usd)}</td>
                <td className="td text-right tabular-nums">{usd(r.answers ? r.cost_usd / r.answers : null, 4)}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td className="td text-ink-2" colSpan={5}>Nothing spent this month yet.</td></tr>}
          </tbody>
        </table>
        <p className="px-4 pb-3 text-xs text-muted">
          Real-user engines are billed by the data provider per answer (exact). API costs are estimated from tokens and
          web searches. Answers saved before cost tracking show as $0.
        </p>
      </section>
    </div>
  );
}
