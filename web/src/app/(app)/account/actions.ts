"use server";

import { getViewer } from "@/lib/auth";
import type { ActionResult } from "@/lib/action-result";
import { createAdminClient, createStatelessClient } from "@/lib/supabase/admin";

/**
 * Change your own password. The current password is checked with a
 * throw-away sign-in (doesn't touch the browser session), then the new one is
 * set through the admin API for THIS user only — so it works without any
 * Supabase email/reauthentication settings.
 */
export async function changeMyPassword(_: ActionResult, formData: FormData): Promise<ActionResult> {
  const viewer = await getViewer();
  const current = String(formData.get("current") ?? "");
  const next = String(formData.get("password") ?? "");
  const confirm = String(formData.get("confirm") ?? "");
  if (next.length < 8) return { error: "New password must be at least 8 characters." };
  if (next !== confirm) return { error: "The two new passwords don't match." };
  if (next === current) return { error: "Choose a different password from your current one." };

  const check = await createStatelessClient().auth.signInWithPassword({ email: viewer.email, password: current });
  if (check.error || check.data.user?.id !== viewer.id) return { error: "Your current password is not correct." };

  try {
    const { error } = await createAdminClient().auth.admin.updateUserById(viewer.id, { password: next });
    if (error) return { error: error.message };
  } catch (e) {
    return { error: (e as Error).message };
  }
  return { ok: "Password changed. Use the new one next time you sign in." };
}
