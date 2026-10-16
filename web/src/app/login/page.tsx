import type { Metadata } from "next";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage(props: PageProps<"/login">) {
  const sp = await props.searchParams;
  const next = typeof sp.next === "string" ? sp.next : "/";

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <div className="card w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">AI Visibility</h1>
        <p className="mb-6 mt-1 text-sm text-ink-2">Sign in to see how AI apps talk about your brand.</p>
        <LoginForm next={next} />
        <p className="mt-6 text-xs text-muted">Accounts are created by your account manager.</p>
      </div>
    </main>
  );
}
