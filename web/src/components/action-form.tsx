"use client";

import { useActionState, useEffect, useRef } from "react";
import type { ActionResult } from "@/lib/action-result";

type Action = (prev: ActionResult, formData: FormData) => Promise<ActionResult>;

/**
 * A <form> bound to a Server Action that shows its error/success message and
 * disables its controls while pending. Resets the fields after success when
 * `resetOnSuccess` is set (e.g. "add question" forms).
 */
export function ActionForm({
  action,
  children,
  className,
  resetOnSuccess = false,
  inlineMessage = false,
}: {
  action: Action;
  children: React.ReactNode;
  className?: string;
  resetOnSuccess?: boolean;
  inlineMessage?: boolean;
}) {
  const [state, formAction, pending] = useActionState<ActionResult, FormData>(action, {});
  const ref = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (resetOnSuccess && state.ok) ref.current?.reset();
  }, [state, resetOnSuccess]);

  const message = state.error ?? state.ok;
  return (
    <form ref={ref} action={formAction} className={className}>
      <fieldset disabled={pending} className="contents">
        {children}
      </fieldset>
      {message && (
        <p
          aria-live="polite"
          className={`${inlineMessage ? "" : "basis-full"} text-xs ${state.error ? "text-critical-ink" : "text-good-ink"}`}
        >
          {message}
        </p>
      )}
    </form>
  );
}
