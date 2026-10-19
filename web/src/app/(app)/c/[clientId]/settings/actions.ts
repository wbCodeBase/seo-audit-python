"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { friendlyError, type ActionResult } from "@/lib/action-result";
import { createClient } from "@/lib/supabase/server";

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
