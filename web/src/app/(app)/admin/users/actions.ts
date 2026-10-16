"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { friendlyError, type ActionResult } from "@/lib/action-result";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";

const PATH = "/admin/users";
const MIN_PASSWORD = 8;

/**
 * Create an account (email + password, no emails sent) and/or give access.
 * If the email already has an account, only the access is applied.
 * Access changes go through the admin's own session, so RLS still applies;
 * the secret key is used only for the Supabase Auth admin call.
 */
export async function createOrGrantUser(_: ActionResult, formData: FormData): Promise<ActionResult> {
  await requireAdmin();
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  const password = String(formData.get("password") ?? "");
  const access = String(formData.get("access") ?? "");          // "admin" or a client id
  const memberRole = formData.get("member_role") === "editor" ? "editor" : "viewer";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return { error: "Enter a valid email address." };
  const clientId = access === "admin" ? null : Number(access);
  if (access !== "admin" && !clientId) return { error: "Choose admin or a client." };

  const supabase = await createClient();
  let { data: profile } = await supabase.from("profiles").select("id").eq("email", email).maybeSingle();
  let created = false;

  if (!profile) {
    if (password.length < MIN_PASSWORD) {
      return { error: `New account: set a password of at least ${MIN_PASSWORD} characters (or click Suggest).` };
    }
    let admin;
    try {
      admin = createAdminClient();
    } catch (e) {
      return { error: (e as Error).message };
    }
    const { data, error } = await admin.auth.admin.createUser({ email, password, email_confirm: true });
    if (error || !data.user) {
      return { error: /already|exists|registered/i.test(error?.message ?? "")
        ? "An account with this email already exists."
        : `Couldn't create the account: ${error?.message ?? "unknown error"}` };
    }
    created = true;
    // The sign-up trigger has just created the profile row.
    profile = { id: data.user.id };
  }

  const now = new Date().toISOString();
  const { error } = clientId
    ? await supabase.from("client_members").upsert(
        { user_id: profile.id, client_id: clientId, role: memberRole, created_at: now },
        { onConflict: "user_id,client_id" })
    : await supabase.from("profiles").update({ role: "admin" }).eq("id", profile.id);
  if (error) return { error: friendlyError(error) };

  revalidatePath(PATH);
  return {
    ok: created
      ? `Account created for ${email}. Send them the sign-in page, their email and the password you set — no email is sent automatically.`
      : `${email} already had an account — access updated.`,
  };
}

export async function resetPassword(userId: string, _: ActionResult, formData: FormData): Promise<ActionResult> {
  await requireAdmin();
  const password = String(formData.get("password") ?? "");
  if (password.length < MIN_PASSWORD) return { error: `At least ${MIN_PASSWORD} characters.` };
  try {
    const { error } = await createAdminClient().auth.admin.updateUserById(userId, { password });
    if (error) return { error: error.message };
  } catch (e) {
    return { error: (e as Error).message };
  }
  return { ok: "Password changed. Share the new one with them." };
}

export async function deleteUser(userId: string): Promise<ActionResult> {
  const me = await requireAdmin();
  if (userId === me.id) return { error: "You can't delete your own account." };
  try {
    // Removes the login; their profile and client access are removed with it.
    const { error } = await createAdminClient().auth.admin.deleteUser(userId);
    if (error) return { error: error.message };
  } catch (e) {
    return { error: (e as Error).message };
  }
  revalidatePath(PATH);
  return { ok: "Deleted." };
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

export async function addMembership(userId: string, _: ActionResult, formData: FormData): Promise<ActionResult> {
  await requireAdmin();
  const clientId = Number(formData.get("client_id"));
  const role = formData.get("member_role") === "editor" ? "editor" : "viewer";
  if (!clientId) return { error: "Choose a client." };
  const supabase = await createClient();
  const { error } = await supabase.from("client_members").upsert(
    { user_id: userId, client_id: clientId, role, created_at: new Date().toISOString() },
    { onConflict: "user_id,client_id" });
  if (error) return { error: friendlyError(error) };
  revalidatePath(PATH);
  return { ok: "Access added." };
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
