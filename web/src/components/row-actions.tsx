"use client";

import { useState, useTransition } from "react";
import type { ActionResult } from "@/lib/action-result";

/** Small buttons that call a bound Server Action, with an optional confirm. */
export function ActionButton({
  action, label, confirm, className = "btn",
}: {
  action: () => Promise<ActionResult>;
  label: string;
  confirm?: string;
  className?: string;
}) {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="inline-flex flex-col items-start">
      <button
        type="button"
        className={className}
        disabled={pending}
        onClick={() => {
          if (confirm && !window.confirm(confirm)) return;
          start(async () => {
            const r = await action();
            setError(r.error ?? null);
          });
        }}
      >
        {pending ? "…" : label}
      </button>
      {error && <span className="mt-1 text-xs text-critical-ink">{error}</span>}
    </span>
  );
}
