import type { Metadata } from "next";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

const ERRORS: Record<string, string> = {
  link: "That sign-in link is invalid or has expired. Request a new one.",
};

export default async function LoginPage(props: PageProps<"/login">) {
  const sp = await props.searchParams;
  const next = typeof sp.next === "string" ? sp.next : "/";
  const error = typeof sp.error === "string" ? ERRORS[sp.error] : undefined;

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <div className="card w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">AI Visibility</h1>
        <p className="mb-6 mt-1 text-sm text-ink-2">Sign in to see how AI apps talk about your brand.</p>
        <LoginForm next={next} initialError={error} />
        <p className="mt-6 text-xs text-muted">Access is by invitation. Ask your account manager if you need one.</p>
      </div>
    </main>
  );
}
