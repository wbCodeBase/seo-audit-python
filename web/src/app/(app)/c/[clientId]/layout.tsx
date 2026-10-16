import { requireClient } from "@/lib/auth";
import { SectionNav } from "@/components/section-nav";

export default async function ClientLayout(props: LayoutProps<"/c/[clientId]">) {
  const { clientId } = await props.params;
  const { client, editable } = await requireClient(clientId);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{client.name}</h1>
          <p className="text-sm text-ink-2">
            {client.domain}
            {client.brand ? ` · brand “${client.brand}”` : ""}
            {!editable && <span className="chip ml-2">View only</span>}
          </p>
        </div>
        <SectionNav base={`/c/${client.id}`} />
      </div>
      {props.children}
    </div>
  );
}
