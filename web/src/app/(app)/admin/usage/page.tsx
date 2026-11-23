import type { Metadata } from "next";
import Link from "next/link";
import { requireAdmin } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { todayIST } from "@/lib/filters";
import { usd } from "@/lib/format";
import { sourceOf, type SpendRow } from "@/lib/types";

export const metadata: Metadata = { title: "Usage" };

export default async function AdminUsagePage() {
  await requireAdmin();
  const supabase = await createClient();
  const today = todayIST();
  const monthStart = today.slice(0, 8) + "01";
  const [{ data: spend, error }, { data: clients }] = await Promise.all([
    supabase.rpc("geo_spend", { p_from: monthStart, p_to: today }),
    supabase.from("clients").select("id, name, monthly_budget_usd").order("name"),
  ]);

  const byClient = new Map<number, { real: number; api: number; answers: number }>();
  for (const r of (spend ?? []) as SpendRow[]) {
    const c = byClient.get(r.client_id) ?? { real: 0, api: 0, answers: 0 };
    if (sourceOf(r.ai_app) === "observed") c.real += r.cost_usd;
    else c.api += r.cost_usd;
    c.answers += r.answers;
    byClient.set(r.client_id, c);
  }
  const total = [...byClient.values()].reduce((s, c) => s + c.real + c.api, 0);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Usage this month</h1>
        <p className="text-sm text-ink-2">{usd(total)} across all clients since {monthStart} (IST).</p>
      </div>
      {error && <p className="rounded-lg bg-critical-wash px-3 py-2 text-sm text-critical-ink">{error.message}</p>}
      <section className="card overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead>
            <tr>
              <th className="th">Client</th>
              <th className="th text-right">Answers</th>
              <th className="th text-right">Real user view</th>
              <th className="th text-right">API estimate</th>
              <th className="th text-right">Total</th>
              <th className="th text-right">Budget</th>
            </tr>
          </thead>
          <tbody>
            {(clients ?? []).map((cl) => {
              const c = byClient.get(cl.id as number) ?? { real: 0, api: 0, answers: 0 };
              const sum = c.real + c.api;
              const budget = cl.monthly_budget_usd as number | null;
              const near = budget != null && sum >= 0.9 * budget;
              return (
                <tr key={cl.id}>
                  <td className="td"><Link className="hover:underline" href={`/c/${cl.id}/usage`}>{cl.name}</Link></td>
                  <td className="td text-right tabular-nums">{c.answers}</td>
                  <td className="td text-right tabular-nums">{usd(c.real)}</td>
                  <td className="td text-right tabular-nums">{usd(c.api)}</td>
                  <td className="td text-right tabular-nums font-medium">{usd(sum)}</td>
                  <td className={`td text-right tabular-nums ${near ? "text-critical-ink" : ""}`}>
                    {budget == null ? <span className="text-muted">No cap</span> : `${usd(budget)}${near ? " · near limit" : ""}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
