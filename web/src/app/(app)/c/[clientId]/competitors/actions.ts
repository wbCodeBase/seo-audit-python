"use server";

import { revalidatePath } from "next/cache";
import { canEdit, getViewer } from "@/lib/auth";
import { friendlyError, type ActionResult } from "@/lib/action-result";
import { createClient } from "@/lib/supabase/server";
import type { Relationship } from "@/lib/types";

const RELATIONSHIPS = new Set<Relationship>(["self", "direct", "indirect", "ignored", "pending"]);

async function guard(clientId: number) {
  const viewer = await getViewer();
  return canEdit(viewer, clientId) ? null : "You don't have permission to change this client's competitors.";
}

export async function setRelationship(clientId: number, competitorId: number, relationship: Relationship): Promise<ActionResult> {
  const denied = await guard(clientId);
  if (denied) return { error: denied };
  if (!RELATIONSHIPS.has(relationship)) return { error: "Unknown relationship." };
  const supabase = await createClient();
  const { error } = await supabase
    .from("competitors")
    .update({ relationship })
    .eq("id", competitorId)
    .eq("client_id", clientId)
    .neq("source", "sheet");
  if (error) return { error: friendlyError(error) };
  revalidatePath(`/c/${clientId}`, "layout");
  return { ok: "Saved." };
}

export async function addCompetitor(clientId: number, _: ActionResult, formData: FormData): Promise<ActionResult> {
  const denied = await guard(clientId);
  if (denied) return { error: denied };
  const domain = String(formData.get("domain") ?? "")
    .trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
  const name = String(formData.get("name") ?? "").trim() || domain;
  if (!domain.includes(".")) return { error: "Enter the competitor's domain, like example.com." };
  const relationship = formData.get("relationship") === "indirect" ? "indirect" : "direct";

  const supabase = await createClient();
  const { error } = await supabase.from("competitors").insert({
    client_id: clientId, key: domain, name, domain, relationship, source: "manual",
    times_seen: 0, created_at: new Date().toISOString(),
  });
  if (error) return { error: error.code === "23505" ? "That competitor is already listed." : friendlyError(error) };
  revalidatePath(`/c/${clientId}/competitors`);
  return { ok: `Added ${name}. It's tracked from the next run.` };
}
