import type { Metadata } from "next";
import { getViewer } from "@/lib/auth";
import { ActionForm } from "@/components/action-form";
import { changeMyPassword } from "./actions";

export const metadata: Metadata = { title: "Account" };

export default async function AccountPage() {
  const viewer = await getViewer();
  return (
    <div className="max-w-md space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Account</h1>
        <p className="text-sm text-ink-2">
          Signed in as <span className="font-medium text-ink">{viewer.email}</span> ·{" "}
          {viewer.role === "admin" ? "Admin" : "Client user"}
        </p>
      </div>
      <section className="card p-4">
        <h2 className="mb-3 font-medium">Change password</h2>
        <ActionForm action={changeMyPassword} resetOnSuccess className="space-y-3">
          <div>
            <label className="label" htmlFor="current">Current password</label>
            <input id="current" name="current" type="password" autoComplete="current-password" required className="input" />
          </div>
          <div>
            <label className="label" htmlFor="password">New password (at least 8 characters)</label>
            <input id="password" name="password" type="password" autoComplete="new-password" minLength={8} required className="input" />
          </div>
          <div>
            <label className="label" htmlFor="confirm">New password again</label>
            <input id="confirm" name="confirm" type="password" autoComplete="new-password" minLength={8} required className="input" />
          </div>
          <button className="btn-primary">Change password</button>
        </ActionForm>
      </section>
    </div>
  );
}
