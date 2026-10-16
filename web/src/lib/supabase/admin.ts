import "server-only";
import { createClient } from "@supabase/supabase-js";

/**
 * Supabase Auth admin API (create accounts, set passwords) using the
 * project's SECRET key. Server-only: the `server-only` import makes the build
 * fail if this file is ever pulled into browser code.
 *
 * Use it ONLY for auth.admin.* calls, and only after requireAdmin() (or the
 * user's own identity) has been checked. Everything else must keep going
 * through the per-user client in ./server.ts so RLS applies.
 */
export function createAdminClient() {
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!key) {
    throw new Error("SUPABASE_SECRET_KEY is not set. Add it in Vercel → Settings → Environment Variables.");
  }
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, key, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}

/** A client that can check an email/password without touching the browser session. */
export function createStatelessClient() {
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}
