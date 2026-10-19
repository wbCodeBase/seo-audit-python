"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

const SECTIONS = [
  { href: "", label: "Overview" },
  { href: "/questions", label: "Questions" },
  { href: "/competitors", label: "Competitors" },
  { href: "/answers", label: "Answers" },
];

export function SectionNav({ base, admin = false }: { base: string; admin?: boolean }) {
  const pathname = usePathname();
  const search = useSearchParams();
  // Keep the date range when moving between sections.
  const range = search.get("range");
  const qs = range ? `?range=${range}` : "";
  return (
    <nav aria-label="Sections" className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1 text-sm">
      {[...SECTIONS, ...(admin ? [{ href: "/settings", label: "Settings" }] : [])].map((s) => {
        const href = base + s.href;
        const active = s.href ? pathname.startsWith(href) : pathname === base;
        return (
          <Link
            key={s.label}
            href={href + qs}
            aria-current={active ? "page" : undefined}
            className={`rounded-md px-3 py-1 ${active ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink"}`}
          >
            {s.label}
          </Link>
        );
      })}
    </nav>
  );
}
