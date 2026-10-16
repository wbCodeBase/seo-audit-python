"use server";

import { revalidatePath } from "next/cache";
import { canEdit, getViewer } from "@/lib/auth";
import { friendlyError, type ActionResult } from "@/lib/action-result";
import { createClient } from "@/lib/supabase/server";

const FUNNEL = new Set(["TOFU", "MOFU", "BOFU"]);
const APPS = new Set(["claude", "chatgpt"]);

async function guard(clientId: number) {
  const viewer = await getViewer();
  if (!canEdit(viewer, clientId)) return "You don't have permission to change this client's questions.";
  return null;
}

export async function addQuestion(clientId: number, _: ActionResult, formData: FormData): Promise<ActionResult> {
  const denied = await guard(clientId);
  if (denied) return { error: denied };

  const text = String(formData.get("text") ?? "").trim().replace(/\s+/g, " ");
  if (text.length < 3) return { error: "Write the question the way a buyer would ask it." };
  if (text.length > 500) return { error: "Keep questions under 500 characters." };
  const funnel = String(formData.get("funnel_stage") ?? "");
  const tags = String(formData.get("tags") ?? "").split(",").map((t) => t.trim()).filter(Boolean).slice(0, 20);
  const aiApps = formData.getAll("ai_apps").map(String).filter((a) => APPS.has(a));

  const supabase = await createClient();
  const now = new Date().toISOString();
  const { error } = await supabase.from("questions").insert({
    client_id: clientId,
    kind: "question",
    text,
    topic: String(formData.get("topic") ?? "").trim() || null,
    funnel_stage: FUNNEL.has(funnel) ? funnel : null,
    tags,
    active: true,
    ai_apps: aiApps.length === APPS.size ? [] : aiApps,
    frequency: "daily",
    source: "platform",
    created_at: now,
    updated_at: now,
  });
  if (error) return { error: error.code === "23505" ? "That question is already tracked." : friendlyError(error) };
  revalidatePath(`/c/${clientId}/questions`);
  return { ok: "Added. It will be asked on the next daily run." };
}

export async function setQuestionActive(clientId: number, questionId: number, active: boolean): Promise<ActionResult> {
  const denied = await guard(clientId);
  if (denied) return { error: denied };
  const supabase = await createClient();
  const { error } = await supabase
    .from("questions")
    .update({ active, updated_at: new Date().toISOString() })
    .eq("id", questionId)
    .eq("client_id", clientId)
    .eq("source", "platform");
  if (error) return { error: friendlyError(error) };
  revalidatePath(`/c/${clientId}/questions`);
  return { ok: active ? "Resumed." : "Paused." };
}

export async function deleteQuestion(clientId: number, questionId: number): Promise<ActionResult> {
  const denied = await guard(clientId);
  if (denied) return { error: denied };
  const supabase = await createClient();
  // Deleting also removes that question's answers and metrics (cascade).
  const { error } = await supabase
    .from("questions")
    .delete()
    .eq("id", questionId)
    .eq("client_id", clientId)
    .eq("source", "platform");
  if (error) return { error: friendlyError(error) };
  revalidatePath(`/c/${clientId}/questions`);
  return { ok: "Deleted." };
}
