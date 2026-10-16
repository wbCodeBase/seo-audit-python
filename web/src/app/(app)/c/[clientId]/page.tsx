import type { Metadata } from "next";
import { requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { parseFilters, rpcArgs } from "@/lib/filters";
import type { CompetitorSov, MatrixCell, MetricsRow } from "@/lib/types";
import { FiltersBar } from "@/components/dashboard/filters-bar";
import { StatTiles } from "@/components/dashboard/stat-tiles";
import { TrendChart } from "@/components/dashboard/trend-chart";
import { ByAppBars, CompetitorBars } from "@/components/dashboard/bars";
import { GeoMatrix } from "@/components/dashboard/geo-matrix";

export const metadata: Metadata = { title: "Overview" };

function Panel({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`card min-w-0 p-4 ${className}`}>
      <h2 className="mb-3 font-medium">{title}</h2>
      {children}
    </section>
  );
}

export default async function OverviewPage(props: PageProps<"/c/[clientId]">) {
  const { clientId } = await props.params;
  const { client } = await requireClient(clientId);
  const filters = parseFilters(await props.searchParams);
  const args = rpcArgs(client.id, filters);
  const supabase = await createClient();

  const [total, trend, byApp, comps, matrix, appRows, topicRows] = await Promise.all([
    supabase.rpc("geo_metrics", { ...args, p_group: "total" }),
    supabase.rpc("geo_metrics", { ...args, p_group: "day" }),
    supabase.rpc("geo_metrics", { ...args, p_group: "app" }),
    supabase.rpc("geo_competitor_sov", args),
    supabase.rpc("geo_matrix", args),
    supabase.from("daily_metrics").select("ai_app").eq("client_id", client.id),
    supabase.from("questions").select("topic").eq("client_id", client.id).not("topic", "is", null),
  ]);

  const error = [total, trend, byApp, comps, matrix].find((r) => r.error)?.error;
  const totalRow = ((total.data ?? []) as MetricsRow[])[0] ?? null;
  const apps = [...new Set((appRows.data ?? []).map((r) => r.ai_app as string))].sort();
  const topics = [...new Set((topicRows.data ?? []).map((r) => r.topic as string))].sort();
  const matrixApps = filters.apps.length ? filters.apps.filter((a) => apps.includes(a)) : apps;

  return (
    <div className="space-y-4">
      <FiltersBar apps={apps} topics={topics} />

      {error && (
        <p className="rounded-lg bg-critical-wash px-3 py-2 text-sm text-critical-ink">
          Couldn&apos;t load all metrics: {error.message}
        </p>
      )}
      {totalRow?.estimated && (
        <p className="rounded-lg bg-warn-wash px-3 py-2 text-sm text-ink-2">
          This period includes imported sheet history, which only recorded links: there, “named” means the AI
          cited your site.
        </p>
      )}

      <StatTiles total={totalRow} />

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Score trend over time" className="lg:col-span-2">
          <TrendChart rows={(trend.data ?? []) as MetricsRow[]} />
        </Panel>
        <Panel title="By AI app">
          <ByAppBars rows={(byApp.data ?? []) as MetricsRow[]} />
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Questions × AI apps" className="lg:col-span-2">
          <GeoMatrix cells={(matrix.data ?? []) as MatrixCell[]} apps={matrixApps} clientId={client.id} />
        </Panel>
        <Panel title="Competitors AI prefers">
          <CompetitorBars you={totalRow?.share_of_voice ?? null} yourName={client.name}
            rows={(comps.data ?? []) as CompetitorSov[]} />
        </Panel>
      </div>
    </div>
  );
}
