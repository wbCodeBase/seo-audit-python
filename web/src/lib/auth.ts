import { redirect } from "next/navigation";
import { cache } from "react";
import { createClient } from "@/lib/supabase/server";
import type { Client, MemberRole, Viewer } from "@/lib/types";

/**
 * The signed-in user, their role and the clients they may open. Cached per
 * request. Redirects to /login when signed out. Everything it reads is
 * filtered by RLS, so a client user only ever gets their own clients back.
 */
export const getViewer = cache(async (): Promise<Viewer> => {
  const supabase = await createClient();
  const { data: claimsData } = await supabase.auth.getClaims();
  const claims = claimsData?.claims;
  if (!claims?.sub) redirect("/login");

  const [{ data: profile }, { data: clients }, { data: members }] = await Promise.all([
    supabase.from("profiles").select("role, email").eq("id", claims.sub).maybeSingle(),
    supabase.from("clients").select("id, name, domain, brand").order("name"),
    supabase.from("client_members").select("client_id, role").eq("user_id", claims.sub),
  ]);

  const memberRoles: Record<number, MemberRole> = {};
  for (const m of members ?? []) memberRoles[m.client_id] = m.role as MemberRole;

  return {
    id: claims.sub,
    email: (profile?.email ?? (claims.email as string | undefined) ?? "") as string,
    role: profile?.role === "admin" ? "admin" : "client",
    clients: (clients ?? []) as Client[],
    memberRoles,
  };
});

export function canEdit(viewer: Viewer, clientId: number) {
  return viewer.role === "admin" || viewer.memberRoles[clientId] === "editor";
}

/** The client a page is about, or a redirect if the viewer can't see it. */
export async function requireClient(clientIdParam: string) {
  const viewer = await getViewer();
  const id = Number(clientIdParam);
  const client = viewer.clients.find((c) => c.id === id);
  if (!client) redirect("/");
  return { viewer, client, editable: canEdit(viewer, client.id) };
}

export async function requireAdmin() {
  const viewer = await getViewer();
  if (viewer.role !== "admin") redirect("/");
  return viewer;
}
