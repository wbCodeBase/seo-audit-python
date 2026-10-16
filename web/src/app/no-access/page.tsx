import type { Metadata } from "next";
import { getViewer } from "@/lib/auth";

export const metadata: Metadata = { title: "No access yet" };

export default async function NoAccess() {
  const viewer = await getViewer();
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <div className="card w-full max-w-md p-6">
        <h1 className="text-lg font-semibold">You&apos;re signed in, but have no access yet</h1>
        <p className="mt-2 text-sm text-ink-2">
          {viewer.email ? <>The account <span className="font-medium text-ink">{viewer.email}</span> </> : "Your account "}
          isn&apos;t linked to any brand yet. Ask your account manager to give you access, then sign in again.
        </p>
        <form action="/auth/signout" method="post" className="mt-6">
          <button className="btn">Sign out</button>
        </form>
      </div>
    </main>
  );
}
