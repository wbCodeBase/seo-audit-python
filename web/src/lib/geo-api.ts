import "server-only";

/**
 * Calls the Python job API (api/manual_run.py on the Python Vercel project)
 * with the shared CRON_SECRET. Server-only: the secret never reaches the
 * browser. Each call is one small step (plan / start / task / finish).
 */
export type GeoAction =
  | { action: "plan" }
  | { action: "start"; client_id: number; trigger: "manual" | "cron"; sync: boolean }
  | { action: "task"; run_id: string; question_id: number; app: string; repeat: number }
  | { action: "finish"; run_id: string; metrics: boolean };

export type GeoResult = { ok: boolean; error?: string; [key: string]: unknown };

export function geoApiConfigured() {
  return Boolean(process.env.GEO_API_URL && process.env.CRON_SECRET);
}

export async function callGeo(payload: GeoAction): Promise<{ status: number; body: GeoResult }> {
  const base = process.env.GEO_API_URL?.replace(/\/+$/, "");
  const secret = process.env.CRON_SECRET;
  if (!base || !secret) {
    return {
      status: 500,
      body: { ok: false, error: "GEO_API_URL and CRON_SECRET must be set on the web project (Vercel → Settings → Environment Variables)." },
    };
  }
  try {
    const res = await fetch(`${base}/api/manual_run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${secret}` },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout(290_000),
    });
    const text = await res.text();
    let body: GeoResult;
    try {
      body = JSON.parse(text);
    } catch {
      // e.g. the platform's own timeout page
      body = { ok: false, error: res.status === 504 ? "The AI app took too long to answer (timeout). Retry it." : `Unexpected response (${res.status}).` };
    }
    return { status: res.status, body };
  } catch (e) {
    const err = e as Error;
    return { status: 502, body: { ok: false, error: err.name === "TimeoutError" ? "Timed out — retry it." : `Couldn't reach the engine: ${err.message}` } };
  }
}
