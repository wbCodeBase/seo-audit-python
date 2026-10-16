"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { friendlyError, type ActionResult } from "@/lib/action-result";
import { createClient as supabaseServer } from "@/lib/supabase/server";

const cleanDomain = (v: string) =>
  v.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];

export async function addClient(_: ActionResult, formData: FormData): Promise<ActionResult> {
  await requireAdmin();
  const name = String(formData.get("name") ?? "").trim();
  const domain = cleanDomain(String(formData.get("domain") ?? ""));
  const brand = String(formData.get("brand") ?? "").trim() || null;
  if (!name || !domain.includes(".")) return { error: "Enter a name and a domain like example.com." };

  const supabase = await supabaseServer();
  const now = new Date().toISOString();
  const { error } = await supabase
    .from("clients")
    .insert({ name, domain, brand, aliases: [], created_at: now, updated_at: now });
  if (error) return { error: friendlyError(error) };
  revalidatePath("/", "layout");
  return { ok: `Added ${name}. Add questions to start tracking it.` };
}
