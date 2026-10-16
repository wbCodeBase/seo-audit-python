export type ActionResult = { error?: string; ok?: string };

/** Turn a Supabase/Postgres error into something a user can act on. */
export function friendlyError(error: { message?: string; code?: string } | null | undefined): string {
  if (!error) return "Something went wrong.";
  if (error.code === "23505") return "That already exists.";
  if (error.code === "42501" || /row-level security/i.test(error.message ?? ""))
    return "You don't have permission to do that.";
  return error.message || "Something went wrong.";
}
