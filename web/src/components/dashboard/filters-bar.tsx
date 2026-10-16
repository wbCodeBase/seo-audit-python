"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";
import { RANGES } from "@/lib/filters";
import { appLabel } from "@/lib/types";

/** One row of filters above the charts; state lives in the URL. */
export function FiltersBar({ apps, topics }: { apps: string[]; topics: string[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const [pending, startTransition] = useTransition();

  const range = sp.get("range") ?? "30";
  const selectedApps = sp.getAll("app");
  const topic = sp.get("topic") ?? "";

  function update(mutate: (p: URLSearchParams) => void) {
    const p = new URLSearchParams(sp.toString());
    mutate(p);
    startTransition(() => router.push(`${pathname}?${p.toString()}`, { scroll: false }));
  }

  return (
    <div className={`flex flex-wrap items-center gap-2 ${pending ? "opacity-60" : ""}`} aria-busy={pending}>
      <label className="sr-only" htmlFor="range">Date range</label>
      <select
        id="range"
        className="input w-auto py-1"
        value={range}
        onChange={(e) => update((p) => p.set("range", e.target.value))}
      >
        {RANGES.map((r) => (
          <option key={r.value} value={r.value}>{r.label}</option>
        ))}
      </select>

      <div role="group" aria-label="AI apps" className="flex flex-wrap gap-1">
        <button
          type="button"
          className={`chip ${selectedApps.length === 0 ? "border-ink bg-surface-2 text-ink" : ""}`}
          aria-pressed={selectedApps.length === 0}
          onClick={() => update((p) => p.delete("app"))}
        >
          All AI apps
        </button>
        {apps.map((a) => {
          const on = selectedApps.includes(a);
          return (
            <button
              key={a}
              type="button"
              aria-pressed={on}
              className={`chip ${on ? "border-ink bg-surface-2 text-ink" : ""}`}
              onClick={() =>
                update((p) => {
                  const next = on ? selectedApps.filter((x) => x !== a) : [...selectedApps, a];
                  p.delete("app");
                  next.forEach((x) => p.append("app", x));
                })
              }
            >
              {appLabel(a)}
            </button>
          );
        })}
      </div>

      {topics.length > 0 && (
        <>
          <label className="sr-only" htmlFor="topic">Topic</label>
          <select
            id="topic"
            className="input w-auto py-1"
            value={topic}
            onChange={(e) => update((p) => (e.target.value ? p.set("topic", e.target.value) : p.delete("topic")))}
          >
            <option value="">All topics</option>
            {topics.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}
