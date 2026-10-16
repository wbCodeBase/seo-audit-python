import type { Metadata } from "next";
import { requireAdmin } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { shortDate } from "@/lib/format";
import { ActionForm } from "@/components/action-form";
import { ActionButton } from "@/components/row-actions";
import { PasswordField } from "@/components/password-field";
import {
  addMembership, createOrGrantUser, deleteUser, removeMember, resetPassword,
  revokeInvitation, setMemberRole, setUserRole,
} from "./actions";

export const metadata: Metadata = { title: "Users" };

type ProfileRow = { id: string; email: string | null; role: "admin" | "client"; created_at: string };
type MemberRow = { user_id: string; client_id: number; role: "viewer" | "editor" };
type InviteRow = {
  id: number; email: string; role: string; client_id: number | null;
  member_role: string | null; created_at: string;
};

const small = "btn px-2 py-0.5 text-xs";

export default async function UsersPage() {
  const me = await requireAdmin();
  const supabase = await createClient();
  const [{ data: profiles }, { data: members }, { data: invites }] = await Promise.all([
    supabase.from("profiles").select("id, email, role, created_at").order("created_at"),
    supabase.from("client_members").select("user_id, client_id, role"),
    supabase.from("invitations").select("id, email, role, client_id, member_role, created_at")
      .is("accepted_at", null).order("created_at", { ascending: false }),
  ]);
  const clientName = new Map(me.clients.map((c) => [c.id, c.name]));
  const memberships = new Map<string, MemberRow[]>();
  for (const m of (members ?? []) as MemberRow[]) {
    memberships.set(m.user_id, [...(memberships.get(m.user_id) ?? []), m]);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Users</h1>
        <p className="text-sm text-ink-2">
          Admins see and manage every client. Client users see only the clients listed against them — viewers can
          look, editors can also manage questions and competitors.
        </p>
      </div>

      <section className="card p-4">
        <h2 className="font-medium">Add a user</h2>
        <p className="mb-3 text-sm text-ink-2">
          Creates their login straight away — no email is sent. Share the sign-in page, their email and this
          password with them yourself. For someone who already has an account, leave the password empty to just
          add access.
        </p>
        <ActionForm action={createOrGrantUser} resetOnSuccess className="flex flex-wrap items-end gap-3">
          <div className="min-w-56 flex-1">
            <label className="label" htmlFor="u-email">Email</label>
            <input id="u-email" name="email" type="email" required className="input" placeholder="name@company.com" />
          </div>
          <PasswordField id="u-password" placeholder="at least 8 characters" />
          <div>
            <label className="label" htmlFor="u-access">Access</label>
            <select id="u-access" name="access" className="input" defaultValue={me.clients[0]?.id ?? "admin"}>
              {me.clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              <option value="admin">Admin (all clients)</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="u-role">Client role</label>
            <select id="u-role" name="member_role" className="input">
              <option value="viewer">Viewer</option>
              <option value="editor">Editor</option>
            </select>
          </div>
          <button className="btn-primary">Save user</button>
        </ActionForm>
      </section>

      <section className="card overflow-x-auto">
        <table className="w-full min-w-[860px]">
          <thead>
            <tr>
              <th className="th">User</th>
              <th className="th">Role</th>
              <th className="th">Client access</th>
              <th className="th">Account</th>
            </tr>
          </thead>
          <tbody>
            {((profiles ?? []) as ProfileRow[]).map((p) => {
              const own = memberships.get(p.id) ?? [];
              const addable = me.clients.filter((c) => !own.some((m) => m.client_id === c.id));
              return (
                <tr key={p.id}>
                  <td className="td">
                    <div className="font-medium">{p.email ?? "—"}{p.id === me.id && <span className="text-muted"> (you)</span>}</div>
                    <div className="text-xs text-muted">Since {shortDate(p.created_at)}</div>
                  </td>
                  <td className="td">
                    <div className="flex flex-wrap items-center gap-2">
                      <span>{p.role === "admin" ? "Admin" : "Client user"}</span>
                      {p.id !== me.id && (
                        <ActionButton className={small}
                          label={p.role === "admin" ? "Make client user" : "Make admin"}
                          confirm={p.role === "admin" ? undefined : `Give ${p.email} access to every client?`}
                          action={setUserRole.bind(null, p.id, p.role === "admin" ? "client" : "admin")} />
                      )}
                    </div>
                  </td>
                  <td className="td">
                    {p.role === "admin" ? (
                      <span className="text-muted">All clients</span>
                    ) : (
                      <div className="space-y-2">
                        {own.length === 0 && <span className="text-muted">None yet</span>}
                        <ul className="space-y-1">
                          {own.map((m) => (
                            <li key={m.client_id} className="flex flex-wrap items-center gap-2">
                              <span>{clientName.get(m.client_id) ?? `#${m.client_id}`}</span>
                              <span className="chip">{m.role}</span>
                              <ActionButton className={small}
                                label={m.role === "viewer" ? "Make editor" : "Make viewer"}
                                action={setMemberRole.bind(null, p.id, m.client_id, m.role === "viewer" ? "editor" : "viewer")} />
                              <ActionButton className={small} label="Remove" action={removeMember.bind(null, p.id, m.client_id)} />
                            </li>
                          ))}
                        </ul>
                        {addable.length > 0 && (
                          <ActionForm action={addMembership.bind(null, p.id)} className="flex flex-wrap items-center gap-2">
                            <select name="client_id" className="input w-auto py-0.5 text-xs" aria-label="Client">
                              {addable.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                            </select>
                            <select name="member_role" className="input w-auto py-0.5 text-xs" aria-label="Role">
                              <option value="viewer">Viewer</option>
                              <option value="editor">Editor</option>
                            </select>
                            <button className={small}>Add access</button>
                          </ActionForm>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="td">
                    <details>
                      <summary className="cursor-pointer text-xs text-ink-2">Password / delete</summary>
                      <div className="mt-2 space-y-2">
                        <ActionForm action={resetPassword.bind(null, p.id)} className="flex flex-wrap items-end gap-2">
                          <PasswordField id={`pw-${p.id}`} label="New password" required />
                          <button className={small}>Set password</button>
                        </ActionForm>
                        {p.id !== me.id && (
                          <ActionButton className={small} label="Delete account"
                            confirm={`Delete ${p.email}'s account? They lose access immediately.`}
                            action={deleteUser.bind(null, p.id)} />
                        )}
                      </div>
                    </details>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {(invites ?? []).length > 0 && (
        <section className="card overflow-x-auto">
          <h2 className="px-4 pt-4 font-medium">Access waiting for an account</h2>
          <p className="px-4 text-sm text-ink-2">
            Applied automatically when an account with this email is created (use “Add a user” above).
          </p>
          <table className="mt-2 w-full min-w-[560px]">
            <tbody>
              {((invites ?? []) as InviteRow[]).map((i) => (
                <tr key={i.id}>
                  <td className="td">{i.email}</td>
                  <td className="td">
                    {i.role === "admin" ? "Admin" : `${clientName.get(i.client_id ?? 0) ?? "client"} · ${i.member_role}`}
                  </td>
                  <td className="td text-muted">Added {shortDate(i.created_at)}</td>
                  <td className="td text-right">
                    <ActionButton className={small} label="Remove" action={revokeInvitation.bind(null, i.id)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
