import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { callGeo, type GeoAction } from "@/lib/geo-api";

// One AI answer can take up to a minute; give the step room.
export const maxDuration = 300;

/**
 * The "Run now" button's backend. Admins, or managers of that client (checked
 * against the database, not just the page), then forwarded to the Python job
 * API with CRON_SECRET. Every step carries client_id and the engine refuses a
 * run that belongs to another client, so a manager can only spend on their own.
 */
export async function POST(request: NextRequest) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) return NextResponse.json({ ok: false, error: "Not signed in." }, { status: 401 });
  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Bad request." }, { status: 400 });
  }
  const int = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v > 0 ? v : null);
  const clientId = int(body.client_id);
  if (!clientId) return NextResponse.json({ ok: false, error: "client_id required." }, { status: 400 });

  const { data: allowed, error: permError } = await supabase.rpc("can_manage_client", { cid: clientId });
  if (permError || allowed !== true) {
    return NextResponse.json({ ok: false, error: "Only admins and this client's managers can run audits." }, { status: 403 });
  }

  let payload: GeoAction;
  switch (body.action) {
    case "start":
      payload = { action: "start", client_id: clientId, trigger: "manual", sync: true, force: body.force === true };
      break;
    case "task": {
      const questionId = int(body.question_id);
      const repeat = int(body.repeat) ?? 1;
      if (typeof body.run_id !== "string" || typeof body.app !== "string" || !questionId) {
        return NextResponse.json({ ok: false, error: "run_id, question_id and app required." }, { status: 400 });
      }
      payload = { action: "task", run_id: body.run_id, question_id: questionId, app: body.app, repeat, client_id: clientId };
      break;
    }
    case "finish":
      if (typeof body.run_id !== "string") {
        return NextResponse.json({ ok: false, error: "run_id required." }, { status: 400 });
      }
      payload = { action: "finish", run_id: body.run_id, metrics: true, client_id: clientId };
      break;
    default:
      return NextResponse.json({ ok: false, error: "Unknown action." }, { status: 400 });
  }

  const { status, body: result } = await callGeo(payload);
  return NextResponse.json(result, { status: status >= 200 && status < 600 ? status : 502 });
}
