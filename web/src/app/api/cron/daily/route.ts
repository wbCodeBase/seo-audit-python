import { NextResponse, type NextRequest } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { callGeo } from "@/lib/geo-api";

export const maxDuration = 300;

const CONCURRENCY = 6;

/**
 * Nightly audit (Vercel Cron, see web/vercel.json). Same job steps as the
 * "Run now" button, for every client with active questions: sync the sheet
 * once, then per client start → ask each question × AI app (6 at a time) →
 * finish; metrics are rebuilt once at the end. Vercel Cron sends
 * "Authorization: Bearer <CRON_SECRET>".
 */
function authorized(request: NextRequest) {
  const secret = process.env.CRON_SECRET;
  if (!secret) return false;
  const given = Buffer.from(request.headers.get("authorization") ?? "");
  const expected = Buffer.from(`Bearer ${secret}`);
  return given.length === expected.length && timingSafeEqual(given, expected);
}

async function pool<T>(items: T[], limit: number, fn: (item: T) => Promise<void>) {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) await fn(items[next++]);
  }));
}

type Task = { question_id: number; app: string; repeat: number };

export async function GET(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });

  const plan = await callGeo({ action: "plan" });
  if (!plan.body.ok) return NextResponse.json(plan.body, { status: 500 });
  const clients = (plan.body.clients as { id: number; name: string }[]) ?? [];

  const summary: Record<string, unknown>[] = [];
  for (const [i, client] of clients.entries()) {
    const started = await callGeo({ action: "start", client_id: client.id, trigger: "cron", sync: false });
    const runId = started.body.run_id as string | null;
    const tasks = (started.body.tasks as Task[]) ?? [];
    if (!started.body.ok || !runId) {
      summary.push({ client: client.name, error: started.body.error ?? started.body.message });
      continue;
    }
    let failed = 0;
    await pool(tasks, CONCURRENCY, async (t) => {
      const r = await callGeo({ action: "task", run_id: runId, question_id: t.question_id, app: t.app, repeat: t.repeat });
      if (!r.body.ok || r.body.error) failed++;
    });
    const last = i === clients.length - 1;
    const done = await callGeo({ action: "finish", run_id: runId, metrics: last });
    summary.push({ client: client.name, run_id: runId, tasks: tasks.length, failed, status: done.body.status });
  }

  return NextResponse.json({ ok: true, sheet: plan.body.sheet, clients: summary });
}
