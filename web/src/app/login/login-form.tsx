"use client";

import { useActionState, useState } from "react";
import { sendMagicLink, signInWithPassword, type LoginState } from "./actions";

export function LoginForm({ next, initialError }: { next: string; initialError?: string }) {
  const [mode, setMode] = useState<"link" | "password">("link");
  const [pwState, pwAction, pwPending] = useActionState<LoginState, FormData>(signInWithPassword, {});
  const [linkState, linkAction, linkPending] = useActionState<LoginState, FormData>(sendMagicLink, {});

  if (mode === "link" && linkState.sent) {
    return (
      <div className="space-y-3 text-sm">
        <p className="font-medium">Check your inbox</p>
        <p className="text-ink-2">
          We sent a sign-in link to <span className="font-medium text-ink">{linkState.sent}</span>. It
          works once and expires in an hour.
        </p>
        <button className="btn w-full" onClick={() => window.location.reload()}>
          Use a different email
        </button>
      </div>
    );
  }

  const state = mode === "link" ? linkState : pwState;
  const pending = mode === "link" ? linkPending : pwPending;

  return (
    <form action={mode === "link" ? linkAction : pwAction} className="space-y-4">
      <input type="hidden" name="next" value={next} />
      <div>
        <label htmlFor="email" className="label">Email</label>
        <input id="email" name="email" type="email" autoComplete="email" required className="input" />
      </div>
      {mode === "password" && (
        <div>
          <label htmlFor="password" className="label">Password</label>
          <input id="password" name="password" type="password" autoComplete="current-password" required className="input" />
        </div>
      )}
      <p aria-live="polite" className="min-h-5 text-sm text-critical-ink">
        {state.error ?? initialError ?? ""}
      </p>
      <button type="submit" className="btn-primary w-full" disabled={pending}>
        {pending ? "Please wait…" : mode === "link" ? "Email me a sign-in link" : "Sign in"}
      </button>
      <button
        type="button"
        className="w-full text-center text-sm text-ink-2 underline-offset-4 hover:underline"
        onClick={() => setMode(mode === "link" ? "password" : "link")}
      >
        {mode === "link" ? "Sign in with a password instead" : "Email me a link instead"}
      </button>
    </form>
  );
}
