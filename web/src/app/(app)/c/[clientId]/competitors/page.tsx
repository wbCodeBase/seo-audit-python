import type { Metadata } from "next";
import { requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { shortDate } from "@/lib/format";
import { todayIST } from "@/lib/filters";
import type { Competitor, CompetitorSov, Relationship } from "@/lib/types";
import { ActionForm } from "@/components/action-form";
import { ActionButton } from "@/components/row-actions";
import { addCompetitor, setRelationship } from "./actions";

export const metadata: Metadata = { title: "Competitors" };

const LABEL: Record<Relationship, string> = {
  direct: "Direct competitor",
  indirect: "Indirect competitor",
  self: "Us (another name for our brand)",
  ignored: "Ignored",
  pending: "Needs review",
};

function Choices({ clientId, c, options }: { clientId: number; c: Competitor; options: Relationship[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((r) => (
        <ActionButton key={r} label={r === "self" ? "It's us" : r === "ignored" ? "Ignore" : r[0].toUpperCase() + r.slice(1)}
          action={setRelationship.bind(null, clientId, c.id, r)} />
      ))}
    </div>
  );
}

export default async function CompetitorsPage(props: PageProps<"/c/[clientId]/competitors">) {
  const { clientId } = await props.params;
  const { client, editable } = await requireClient(clientId);
  const supabase = await createClient();
  const [{ data }, { data: sov }] = await Promise.all([
    supabase
      .from("competitors")
      .select("id, client_id, key, name, domain, relationship, source, times_seen, first_seen_at, last_seen_at")
      .eq("client_id", client.id)
      .order("times_seen", { ascending: false }),
    // All-time count of answers that named each tracked competitor.
    supabase.rpc("geo_competitor_sov", { p_client: client.id, p_from: "2000-01-01", p_to: todayIST() }),
  ]);
  const all = (data ?? []) as Competitor[];
  const named = new Map(((sov ?? []) as CompetitorSov[]).map((r) => [r.competitor_id, r.mentions]));
  const pending = all.filter((c) => c.relationship === "pending");
  const tracked = all.filter((c) => ["direct", "indirect", "self"].includes(c.relationship));
  const ignored = all.filter((c) => c.relationship === "ignored");

  return (
    <div className="space-y-4">
      <section className="card p-4">
        <h2 className="font-medium">Suggested by AI answers</h2>
        <p className="mb-3 text-sm text-ink-2">
          Brands the AI apps recommended that you don&apos;t track yet. Direct competitors count towards share of
          voice and mention rate; indirect ones are tracked but don&apos;t.
        </p>
        {pending.length === 0 ? (
          <p className="text-sm text-muted">Nothing to review right now.</p>
        ) : (
          <ul className="divide-y divide-line">
            {pending.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div>
                  <div className="font-medium">{c.name}</div>
                  <div className="text-xs text-muted">
                    {c.domain ?? "no website named"} · seen in {c.times_seen} answer{c.times_seen === 1 ? "" : "s"}
                    {c.last_seen_at ? `, last ${shortDate(c.last_seen_at)}` : ""}
                  </div>
                </div>
                {editable && <Choices clientId={client.id} c={c} options={["direct", "indirect", "self", "ignored"]} />}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card overflow-x-auto">
        <h2 className="px-4 pt-4 font-medium">Tracked</h2>
        <table className="mt-2 w-full min-w-[640px]">
          <thead>
            <tr>
              <th className="th">Competitor</th>
              <th className="th">Relationship</th>
              <th className="th">Answers naming them</th>
              {editable && <th className="th"><span className="sr-only">Change</span></th>}
            </tr>
          </thead>
          <tbody>
            {tracked.map((c) => (
              <tr key={c.id}>
                <td className="td">
                  <div className="font-medium">{c.name}</div>
                  {c.domain && c.domain !== c.name && <div className="text-xs text-muted">{c.domain}</div>}
                </td>
                <td className="td">{LABEL[c.relationship]}</td>
                <td className="td tabular-nums">{named.get(c.id) ?? 0}</td>
                {editable && (
                  <td className="td">
                    {c.source === "sheet" ? (
                      <span className="text-xs text-muted">Listed in the sheet</span>
                    ) : (
                      <Choices clientId={client.id} c={c}
                        options={(["direct", "indirect", "ignored"] as Relationship[]).filter((r) => r !== c.relationship)} />
                    )}
                  </td>
                )}
              </tr>
            ))}
            {tracked.length === 0 && <tr><td className="td text-ink-2" colSpan={4}>No competitors tracked yet.</td></tr>}
          </tbody>
        </table>
      </section>

      {editable && (
        <section className="card p-4">
          <h2 className="mb-3 font-medium">Add a competitor</h2>
          <ActionForm action={addCompetitor.bind(null, client.id)} resetOnSuccess className="flex flex-wrap items-end gap-3">
            <div className="min-w-48 flex-1">
              <label className="label" htmlFor="c-domain">Domain</label>
              <input id="c-domain" name="domain" required className="input" placeholder="competitor.com" />
            </div>
            <div className="min-w-48 flex-1">
              <label className="label" htmlFor="c-name">Name (as written in text)</label>
              <input id="c-name" name="name" className="input" placeholder="Competitor Inc" />
            </div>
            <div>
              <label className="label" htmlFor="c-rel">Relationship</label>
              <select id="c-rel" name="relationship" className="input">
                <option value="direct">Direct</option>
                <option value="indirect">Indirect</option>
              </select>
            </div>
            <button className="btn-primary">Add</button>
          </ActionForm>
        </section>
      )}

      {ignored.length > 0 && (
        <details className="card p-4 text-sm">
          <summary className="cursor-pointer font-medium">Ignored ({ignored.length})</summary>
          <ul className="mt-2 divide-y divide-line">
            {ignored.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 py-2">
                <span>{c.name}{c.domain && c.domain !== c.name ? ` · ${c.domain}` : ""}</span>
                {editable && <ActionButton label="Restore for review" action={setRelationship.bind(null, client.id, c.id, "pending")} />}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
