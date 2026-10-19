import type { Metadata } from "next";
import { requireAdmin, requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { dateTime } from "@/lib/format";
import { shiftDays, todayIST } from "@/lib/filters";
import { ActionForm } from "@/components/action-form";
import { updateClient } from "./actions";

export const metadata: Metadata = { title: "Settings" };

type RunRow = { id: string; trigger: string; status: string; started_at: string; finished_at: string | null };

const STATUS: Record<string, string> = {
  done: "Done", partial: "Partly done", failed: "Failed", running: "Running / unfinished",
};

export default async function SettingsPage(props: PageProps<"/c/[clientId]/settings">) {
  const { clientId } = await props.params;
  await requireAdmin();
  const { client } = await requireClient(clientId);
  const supabase = await createClient();

  const since = shiftDays(todayIST(), -60);
  const [{ data: full }, { data: answers }] = await Promise.all([
    supabase.from("clients").select("aliases").eq("id", client.id).maybeSingle(),
    supabase.from("answers").select("run_id, error, ai_app").eq("client_id", client.id)
      .gte("created_at", since).limit(5000),
  ]);

  // Group this client's answers by run; runs cover all clients, so count only ours.
  const perRun = new Map<string, { answers: number; errors: number; apps: Set<string> }>();
  for (const a of answers ?? []) {
    const r = perRun.get(a.run_id) ?? { answers: 0, errors: 0, apps: new Set<string>() };
    r.answers++;
    if (a.error) r.errors++;
    r.apps.add(a.ai_app);
    perRun.set(a.run_id, r);
  }
  const { data: runs } = perRun.size
    ? await supabase.from("runs").select("id, trigger, status, started_at, finished_at")
        .in("id", [...perRun.keys()]).order("started_at", { ascending: false }).limit(30)
    : { data: [] as RunRow[] };

  return (
    <div className="space-y-4">
      <section className="card p-4">
        <h2 className="mb-3 font-medium">Client details</h2>
        <ActionForm action={updateClient.bind(null, client.id)} className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="s-name">Name</label>
            <input id="s-name" name="name" required defaultValue={client.name} className="input" />
          </div>
          <div>
            <label className="label" htmlFor="s-domain">Domain</label>
            <input id="s-domain" name="domain" required defaultValue={client.domain} className="input" />
          </div>
          <div>
            <label className="label" htmlFor="s-brand">Brand name (as written in answers)</label>
            <input id="s-brand" name="brand" defaultValue={client.brand ?? ""} className="input" />
          </div>
          <div>
            <label className="label" htmlFor="s-aliases">Other names (comma-separated)</label>
            <input id="s-aliases" name="aliases" defaultValue={((full?.aliases as string[]) ?? []).join(", ")}
              className="input" placeholder="WhiteBunnie, White Bunnie Digital" />
          </div>
          <p className="text-xs text-muted md:col-span-2">
            Other names count as your brand when the AI writes them, e.g. spellings without a space or an older name.
            If this client also comes from the Google Sheet, its name must match the sheet&apos;s Project column.
          </p>
          <div className="md:col-span-2"><button className="btn-primary">Save</button></div>
        </ActionForm>
      </section>

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 font-medium">Run history (last 60 days)</h2>
        <p className="px-4 text-sm text-ink-2">
          Nightly runs start at 21:00 IST. “Run now” runs appear here too.
        </p>
        <table className="mt-2 w-full min-w-[640px]">
          <thead>
            <tr>
              <th className="th">Started</th>
              <th className="th">How</th>
              <th className="th">Status</th>
              <th className="th text-right">Answers</th>
              <th className="th text-right">Failed</th>
              <th className="th">AI apps</th>
            </tr>
          </thead>
          <tbody>
            {((runs ?? []) as RunRow[]).map((r) => {
              const c = perRun.get(r.id)!;
              return (
                <tr key={r.id}>
                  <td className="td">{dateTime(r.started_at)}</td>
                  <td className="td">{r.trigger === "cron" ? "Nightly" : r.trigger === "import" ? "Imported" : "Run now"}</td>
                  <td className="td">{STATUS[r.status] ?? r.status}</td>
                  <td className="td text-right tabular-nums">{c.answers}</td>
                  <td className={`td text-right tabular-nums ${c.errors ? "text-critical-ink" : ""}`}>{c.errors}</td>
                  <td className="td">{[...c.apps].sort().join(", ")}</td>
                </tr>
              );
            })}
            {(runs ?? []).length === 0 && (
              <tr><td className="td text-ink-2" colSpan={6}>No runs in the last 60 days. Use “Run now”.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
