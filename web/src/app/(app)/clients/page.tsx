import type { Metadata } from "next";
import Link from "next/link";
import { getViewer } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { shiftDays, todayIST } from "@/lib/filters";
import { pct, score } from "@/lib/format";
import type { MetricsRow } from "@/lib/types";
import { ActionForm } from "@/components/action-form";
import { addClient } from "./actions";

export const metadata: Metadata = { title: "Clients" };

export default async function ClientsPage() {
  const viewer = await getViewer();
  const supabase = await createClient();
  const to = todayIST();
  const from = shiftDays(to, -29);

  const [summaries, { data: questions }] = await Promise.all([
    Promise.all(
      viewer.clients.map(async (c) => {
        const { data } = await supabase.rpc("geo_metrics", { p_client: c.id, p_from: from, p_to: to });
        return [c.id, (data?.[0] ?? null) as MetricsRow | null] as const;
      }),
    ),
    supabase.from("questions").select("client_id").eq("active", true),
  ]);
  const byClient = new Map(summaries);
  const questionCount = new Map<number, number>();
  for (const q of questions ?? []) questionCount.set(q.client_id, (questionCount.get(q.client_id) ?? 0) + 1);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Clients</h1>
        <p className="text-sm text-ink-2">Last 30 days. Open a client for its full dashboard.</p>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead>
            <tr>
              <th className="th">Client</th>
              <th className="th">Active questions</th>
              <th className="th text-right">Visibility</th>
              <th className="th text-right">Mention rate</th>
              <th className="th text-right">Share of voice</th>
            </tr>
          </thead>
          <tbody>
            {viewer.clients.map((c) => {
              const m = byClient.get(c.id);
              return (
                <tr key={c.id} className="hover:bg-surface-2">
                  <td className="td">
                    <Link href={`/c/${c.id}`} className="font-medium hover:underline">{c.name}</Link>
                    <div className="text-xs text-muted">{c.domain}</div>
                  </td>
                  <td className="td tabular-nums">{questionCount.get(c.id) ?? 0}</td>
                  <td className="td text-right tabular-nums">{score(m?.visibility_score)}</td>
                  <td className="td text-right tabular-nums">{pct(m?.mention_rate)}</td>
                  <td className="td text-right tabular-nums">{pct(m?.share_of_voice)}</td>
                </tr>
              );
            })}
            {viewer.clients.length === 0 && (
              <tr><td className="td text-ink-2" colSpan={5}>No clients yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {viewer.role === "admin" && (
        <section className="card p-4">
          <h2 className="mb-3 font-medium">Add a client</h2>
          <ActionForm action={addClient} resetOnSuccess className="flex flex-wrap items-end gap-3">
            <div className="min-w-48 flex-1">
              <label className="label" htmlFor="name">Name</label>
              <input id="name" name="name" required className="input" placeholder="SaasWorx" />
            </div>
            <div className="min-w-48 flex-1">
              <label className="label" htmlFor="domain">Domain</label>
              <input id="domain" name="domain" required className="input" placeholder="saasworx.ai" />
            </div>
            <div className="min-w-48 flex-1">
              <label className="label" htmlFor="brand">Brand name (as written in text)</label>
              <input id="brand" name="brand" className="input" placeholder="SaasWorx" />
            </div>
            <button className="btn-primary">Add client</button>
          </ActionForm>
        </section>
      )}
    </div>
  );
}
