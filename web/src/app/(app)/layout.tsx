import Link from "next/link";
import { getViewer } from "@/lib/auth";
import { ClientSwitcher } from "@/components/client-switcher";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const viewer = await getViewer();
  return (
    <div className="flex flex-1 flex-col">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
          <Link href="/" className="font-semibold">AI Visibility</Link>
          {viewer.clients.length > 0 && (
            <ClientSwitcher clients={viewer.clients.map(({ id, name }) => ({ id, name }))} />
          )}
          <nav className="ml-auto flex items-center gap-3 text-sm">
            {viewer.role === "admin" && (
              <>
                <Link href="/clients" className="text-ink-2 hover:text-ink">Clients</Link>
                <Link href="/admin/users" className="text-ink-2 hover:text-ink">Users</Link>
              </>
            )}
            <Link href="/account" className="hidden text-muted hover:text-ink sm:inline" title="Account and password">
              {viewer.email}
            </Link>
            <form action="/auth/signout" method="post">
              <button className="btn">Sign out</button>
            </form>
          </nav>
        </div>
      </header>
      <div className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">{children}</div>
    </div>
  );
}
