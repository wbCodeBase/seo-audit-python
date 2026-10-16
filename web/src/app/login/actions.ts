"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type LoginState = { error?: string };

function safeNext(value: FormDataEntryValue | null) {
  const v = typeof value === "string" ? value : "";
  // Only same-site relative paths — never redirect to another origin.
  return v.startsWith("/") && !v.startsWith("//") ? v : "/";
}

export async function signIn(_: LoginState, formData: FormData): Promise<LoginState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!email || !password) return { error: "Enter your email and password." };

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    return {
      error: /rate|too many/i.test(error.message)
        ? "Too many attempts. Wait a minute and try again."
        : "Wrong email or password. Forgot it? Ask your account manager to reset it.",
    };
  }
  redirect(safeNext(formData.get("next")));
}
