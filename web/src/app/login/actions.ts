"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type LoginState = { error?: string; sent?: string };

function safeNext(value: FormDataEntryValue | null) {
  const v = typeof value === "string" ? value : "";
  // Only same-site relative paths — never redirect to another origin.
  return v.startsWith("/") && !v.startsWith("//") ? v : "/";
}

export async function signInWithPassword(_: LoginState, formData: FormData): Promise<LoginState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!email || !password) return { error: "Enter your email and password." };

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { error: "Wrong email or password. No password yet? Use “Email me a link”." };
  redirect(safeNext(formData.get("next")));
}

export async function sendMagicLink(_: LoginState, formData: FormData): Promise<LoginState> {
  const email = String(formData.get("email") ?? "").trim();
  if (!email) return { error: "Enter your email." };

  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const origin = h.get("origin") ?? `${proto}://${host}`;
  const next = safeNext(formData.get("next"));

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: `${origin}/auth/callback?next=${encodeURIComponent(next)}` },
  });
  if (error) {
    return {
      error: error.message.toLowerCase().includes("rate")
        ? "Too many emails sent. Wait a few minutes and try again."
        : "Couldn't send the link. Check the address and try again.",
    };
  }
  return { sent: email };
}
