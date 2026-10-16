"use client";

import { useParams, usePathname, useRouter } from "next/navigation";

/** Jump between clients, staying on the same section (overview, questions…). */
export function ClientSwitcher({ clients }: { clients: { id: number; name: string }[] }) {
  const router = useRouter();
  const params = useParams<{ clientId?: string }>();
  const pathname = usePathname();
  const current = params.clientId ?? "";

  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="sr-only">Client</span>
      <select
        className="input w-auto py-1"
        value={current}
        onChange={(e) => {
          const id = e.target.value;
          if (!id) return;
          const section = current ? pathname.split("/").slice(3).join("/") : "";
          router.push(`/c/${id}${section ? "/" + section : ""}`);
        }}
      >
        {!current && <option value="">Select a client…</option>}
        {clients.map((c) => (
          <option key={c.id} value={c.id}>{c.name}</option>
        ))}
      </select>
    </label>
  );
}
