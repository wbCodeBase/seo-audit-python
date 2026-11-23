// How far the API answers can be trusted: the latest answer per question ×
// engine, paired with its real-user counterpart (same family), compared on
// whether the client is named, where, and which sources are cited.
import { ENGINES, engineInfo, sourceOf } from "@/lib/types";

export type CompareAnswer = {
  id: string;
  question_id: number;
  ai_app: string;
  created_at: string;
  usage: { observed?: { ai_overview_shown?: boolean } } | null;
  mentions: { entity_type: string; mentioned: boolean | null; mention_position: number | null; cited: boolean }[];
  citations: { domain: string }[];
};

export type Cell = {
  answerId: string;
  named: boolean;
  position: number | null;
  cited: boolean;
  domains: string[];
  noOverview: boolean;   // Google showed no AI answer for this query
};

export type Verdict = "agree" | "api_over" | "api_under";

export type PairStats = {
  api: string;
  real: string;
  compared: number;
  agree: number;
  apiOver: number;     // API says you're named, real users don't see you
  apiUnder: number;    // real users see you, API says you're missing
  overlap: number | null;      // avg Jaccard of cited domains, 0-100
  positionGap: number | null;  // avg |API position − real position| when both name you
};

export type QuestionRow = { questionId: number; cells: Record<string, Cell>; verdicts: Record<string, Verdict> };

const OURS = new Set(["own", "own brand"]);

export function latestCells(answers: CompareAnswer[]) {
  const out = new Map<number, Record<string, Cell>>();
  // answers arrive newest first; keep the first seen per question × engine
  for (const a of answers) {
    const row = out.get(a.question_id) ?? {};
    if (row[a.ai_app]) continue;
    const ours = a.mentions.filter((m) => OURS.has(m.entity_type));
    const positions = ours.map((m) => m.mention_position).filter((p): p is number => p != null);
    row[a.ai_app] = {
      answerId: a.id,
      named: ours.some((m) => m.mentioned ?? m.cited),
      position: positions.length ? Math.min(...positions) : null,
      cited: ours.some((m) => m.cited),
      domains: [...new Set(a.citations.map((c) => c.domain))],
      noOverview: a.usage?.observed?.ai_overview_shown === false,
    };
    out.set(a.question_id, row);
  }
  return out;
}

const jaccard = (a: string[], b: string[]) => {
  if (!a.length && !b.length) return null;
  const sa = new Set(a);
  const inter = b.filter((d) => sa.has(d)).length;
  return (100 * inter) / new Set([...a, ...b]).size;
};

/** API engines that have a real-user counterpart in the data. */
export function pairsIn(apps: string[]) {
  const pairs: { api: string; real: string }[] = [];
  for (const api of apps.filter((a) => sourceOf(a) === "api")) {
    const fam = engineInfo(api)?.family;
    const real = ENGINES.find((e) => e.source === "observed" && e.family === fam && apps.includes(e.key));
    if (real) pairs.push({ api, real: real.key });
  }
  return pairs;
}

export function compare(answers: CompareAnswer[]) {
  const cells = latestCells(answers);
  const apps = [...new Set(answers.map((a) => a.ai_app))];
  const pairs = pairsIn(apps);
  const rows: QuestionRow[] = [];
  const stats: PairStats[] = pairs.map((p) => ({ ...p, compared: 0, agree: 0, apiOver: 0, apiUnder: 0, overlap: null, positionGap: null }));
  const overlaps = stats.map(() => [] as number[]);
  const gaps = stats.map(() => [] as number[]);

  for (const [questionId, row] of cells) {
    const verdicts: Record<string, Verdict> = {};
    pairs.forEach((p, i) => {
      const a = row[p.api];
      const r = row[p.real];
      if (!a || !r) return;
      const s = stats[i];
      s.compared++;
      if (a.named === r.named) {
        s.agree++;
        verdicts[p.api] = "agree";
      } else if (a.named) {
        s.apiOver++;
        verdicts[p.api] = "api_over";
      } else {
        s.apiUnder++;
        verdicts[p.api] = "api_under";
      }
      const j = jaccard(a.domains, r.domains);
      if (j != null) overlaps[i].push(j);
      if (a.position != null && r.position != null) gaps[i].push(Math.abs(a.position - r.position));
    });
    rows.push({ questionId, cells: row, verdicts });
  }
  const avg = (xs: number[]) => (xs.length ? xs.reduce((x, y) => x + y, 0) / xs.length : null);
  stats.forEach((s, i) => {
    s.overlap = avg(overlaps[i]);
    s.positionGap = avg(gaps[i]);
  });
  const realApps = apps.filter((a) => sourceOf(a) === "observed");
  const apiApps = apps.filter((a) => sourceOf(a) === "api");
  const order = (xs: string[]) => ENGINES.map((e) => e.key).filter((k) => xs.includes(k));
  return { rows, stats, realApps: order(realApps), apiApps: order(apiApps) };
}
