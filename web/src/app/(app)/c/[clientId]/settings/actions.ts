"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { friendlyError, type ActionResult } from "@/lib/action-result";
import { createClient } from "@/lib/supabase/server";
import { ENGINE_KEYS } from "@/lib/types";

const cleanDomain = (v: string) =>
  v.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];

export async function updateClient(clientId: number, _: ActionResult, formData: FormData): Promise<ActionResult> {
  await requireAdmin();
  const name = String(formData.get("name") ?? "").trim();
  const domain = cleanDomain(String(formData.get("domain") ?? ""));
  const brand = String(formData.get("brand") ?? "").trim() || null;
  const aliases = String(formData.get("aliases") ?? "")
    .split(",").map((a) => a.trim()).filter(Boolean).slice(0, 20);
  if (!name || !domain.includes(".")) return { error: "Enter a name and a domain like example.com." };

  const supabase = await createClient();
  const { error } = await supabase
    .from("clients")
    .update({ name, domain, brand, aliases, updated_at: new Date().toISOString() })
    .eq("id", clientId);
  if (error) return { error: friendlyError(error) };
  revalidatePath("/", "layout");
  return { ok: "Saved. Changes apply from the next run." };
}

/** Which engines this client is tracked on, and its monthly spending cap. */
export async function updateTracking(clientId: number, _: ActionResult, formData: FormData): Promise<ActionResult> {
  await requireAdmin();
  const picked = formData.getAll("engines").map(String).filter((e) => ENGINE_KEYS.includes(e));
  if (picked.length === 0) return { error: "Choose at least one engine." };
  const rawBudget = String(formData.get("budget") ?? "").trim();
  const budget = rawBudget === "" ? null : Number(rawBudget);
  if (budget != null && (!Number.isFinite(budget) || budget < 0 || budget > 100000)) {
    return { error: "Budget must be a dollar amount, or empty for no cap." };
  }
  const supabase = await createClient();
  const { error } = await supabase.from("clients").update({
    engines: picked.length === ENGINE_KEYS.length ? [] : picked,
    monthly_budget_usd: budget,
    updated_at: new Date().toISOString(),
  }).eq("id", clientId);
  if (error) return { error: friendlyError(error) };
  revalidatePath(`/c/${clientId}`, "layout");
  return { ok: "Saved. The next run uses these engines and stops before passing the budget." };
}
