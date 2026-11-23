// Dashboard filters live in the URL (?range=30&source=observed&app=claude&topic=NetSuite)
// so views are shareable and the server renders exactly what was asked for.
import { ENGINES, type Source } from "@/lib/types";

export const RANGES = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "365", label: "Last 12 months" },
] as const;

/** Today's date in IST — the day boundary the metrics job uses. */
export function todayIST(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

export function shiftDays(isoDate: string, days: number): string {
  const d = new Date(isoDate + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

type SP = Record<string, string | string[] | undefined>;

const list = (v: string | string[] | undefined) =>
  (Array.isArray(v) ? v : v ? [v] : []).filter(Boolean);

export function parseFilters(sp: SP) {
  const rangeRaw = typeof sp.range === "string" ? sp.range : "30";
  const range = RANGES.some((r) => r.value === rangeRaw) ? rangeRaw : "30";
  const to = todayIST();
  const from = shiftDays(to, -(Number(range) - 1));
  const apps = list(sp.app);
  const topics = list(sp.topic);
  const rawSource = typeof sp.source === "string" ? sp.source : "";
  const source: Source | "all" = rawSource === "observed" || rawSource === "api" ? rawSource : "all";
  return { range, from, to, apps, topics, source };
}

/** Engines the filters select: the app chips, narrowed to the data source. */
export function selectedApps(f: { apps: string[]; source: Source | "all" }): string[] | null {
  if (f.source === "all") return f.apps.length ? f.apps : null;
  const inSource = ENGINES.filter((e) => e.source === f.source).map((e) => e.key);
  const picked = f.apps.length ? f.apps.filter((a) => inSource.includes(a)) : inSource;
  return picked.length ? picked : ["__none__"];
}

export type Filters = ReturnType<typeof parseFilters>;

/** RPC args shared by every dashboard function (null = no filter). */
export function rpcArgs(clientId: number, f: Filters) {
  return {
    p_client: clientId,
    p_from: f.from,
    p_to: f.to,
    p_apps: selectedApps(f),
    p_topics: f.topics.length ? f.topics : null,
  };
}
