import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { callGeo, type GeoAction } from "@/lib/geo-api";

// One AI answer can take up to a minute; give the step room.
export const maxDuration = 300;

/**
 * The "Run now" button's backend. Admins only (checked against the database,
 * not just the page), then forwarded to the Python job API with CRON_SECRET.
 */
export async function POST(request: NextRequest) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) return NextResponse.json({ ok: false, error: "Not signed in." }, { status: 401 });
  const { data: profile } = await supabase.from("profiles").select("role").eq("id", userId).maybeSingle();
  if (profile?.role !== "admin") {
    return NextResponse.json({ ok: false, error: "Only admins can run audits." }, { status: 403 });
  }

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Bad request." }, { status: 400 });
  }

  let payload: GeoAction;
  const int = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v > 0 ? v : null);
  switch (body.action) {
    case "start": {
      const clientId = int(body.client_id);
      if (!clientId) return NextResponse.json({ ok: false, error: "client_id required." }, { status: 400 });
      payload = { action: "start", client_id: clientId, trigger: "manual", sync: true };
      break;
    }
    case "task": {
      const questionId = int(body.question_id);
      const repeat = int(body.repeat) ?? 1;
      if (typeof body.run_id !== "string" || typeof body.app !== "string" || !questionId) {
        return NextResponse.json({ ok: false, error: "run_id, question_id and app required." }, { status: 400 });
      }
      payload = { action: "task", run_id: body.run_id, question_id: questionId, app: body.app, repeat };
      break;
    }
    case "finish":
      if (typeof body.run_id !== "string") {
        return NextResponse.json({ ok: false, error: "run_id required." }, { status: 400 });
      }
      payload = { action: "finish", run_id: body.run_id, metrics: true };
      break;
    default:
      return NextResponse.json({ ok: false, error: "Unknown action." }, { status: 400 });
  }

  const { status, body: result } = await callGeo(payload);
  return NextResponse.json(result, { status: status >= 200 && status < 600 ? status : 502 });
}
