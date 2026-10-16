"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { friendlyError, type ActionResult } from "@/lib/action-result";
import { createClient } from "@/lib/supabase/server";

const PATH = "/admin/users";

/**
 * Give an email access. If they've already signed in once, it applies now;
 * otherwise it's stored as an invitation and applied on their first sign-in
 * (database trigger). Mirrors geo/admin.py invite().
 */
export async function inviteUser(_: ActionResult, formData: FormData): Promise<ActionResult> {
  const me = await requireAdmin();
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  const access = String(formData.get("access") ?? "");          // "admin" or a client id
  const memberRole = formData.get("member_role") === "editor" ? "editor" : "viewer";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return { error: "Enter a valid email address." };
  const clientId = access === "admin" ? null : Number(access);
  if (access !== "admin" && !clientId) return { error: "Choose admin or a client." };

  const supabase = await createClient();
  const { data: profile } = await supabase.from("profiles").select("id").eq("email", email).maybeSingle();
  const now = new Date().toISOString();

  if (profile) {
    const { error } = clientId
      ? await supabase.from("client_members").upsert(
          { user_id: profile.id, client_id: clientId, role: memberRole, created_at: now },
          { onConflict: "user_id,client_id" })
      : await supabase.from("profiles").update({ role: "admin" }).eq("id", profile.id);
    if (error) return { error: friendlyError(error) };
    revalidatePath(PATH);
    return { ok: `${email} already had an account — access granted now.` };
  }

  let existing = supabase.from("invitations").select("id").eq("email", email);
  existing = clientId ? existing.eq("client_id", clientId) : existing.is("client_id", null);
  const { data: found } = await existing.maybeSingle();
  const row = { email, role: clientId ? "client" : "admin", client_id: clientId,
                member_role: clientId ? memberRole : null, invited_by: me.id };
  const { error } = found
    ? await supabase.from("invitations").update(row).eq("id", found.id)
    : await supabase.from("invitations").insert({ ...row, created_at: now });
  if (error) return { error: friendlyError(error) };
  revalidatePath(PATH);
  return { ok: `Invited ${email}. They get access the first time they sign in on the login page.` };
}

export async function setUserRole(userId: string, role: "admin" | "client"): Promise<ActionResult> {
  const me = await requireAdmin();
  if (userId === me.id && role !== "admin") return { error: "You can't remove your own admin access." };
  const supabase = await createClient();
  const { error } = await supabase.from("profiles").update({ role }).eq("id", userId);
  if (error) return { error: friendlyError(error) };
  revalidatePath(PATH);
  return { ok: "Saved." };
}

export async function setMemberRole(userId: string, clientId: number, role: "viewer" | "editor"): Promise<ActionResult> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase.from("client_members").update({ role }).eq("user_id", userId).eq("client_id", clientId);
  if (error) return { error: friendlyError(error) };
  revalidatePath(PATH);
  return { ok: "Saved." };
}

export async function removeMember(userId: string, clientId: number): Promise<ActionResult> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase.from("client_members").delete().eq("user_id", userId).eq("client_id", clientId);
  if (error) return { error: friendlyError(error) };
  revalidatePath(PATH);
  return { ok: "Removed." };
}

export async function revokeInvitation(invitationId: number): Promise<ActionResult> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase.from("invitations").delete().eq("id", invitationId);
  if (error) return { error: friendlyError(error) };
  revalidatePath(PATH);
  return { ok: "Revoked." };
}
