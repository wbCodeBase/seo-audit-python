import { requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { SectionNav } from "@/components/section-nav";
import { RunNow } from "@/components/run-now";

export default async function ClientLayout(props: LayoutProps<"/c/[clientId]">) {
  const { clientId } = await props.params;
  const { viewer, client, editable } = await requireClient(clientId);
  const isAdmin = viewer.role === "admin";

  let activeQuestions = 0;
  if (isAdmin) {
    const supabase = await createClient();
    const { count } = await supabase
      .from("questions")
      .select("id", { count: "exact", head: true })
      .eq("client_id", client.id)
      .eq("active", true);
    activeQuestions = count ?? 0;
  }

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">{client.name}</h1>
            <p className="text-sm text-ink-2">
              {client.domain}
              {client.brand ? ` · brand “${client.brand}”` : ""}
              {!editable && <span className="chip ml-2">View only</span>}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <SectionNav base={`/c/${client.id}`} admin={isAdmin} />
          </div>
        </div>
        {isAdmin && (
          <div className="flex justify-end">
            <RunNow clientId={client.id} activeQuestions={activeQuestions} />
          </div>
        )}
      </div>
      {props.children}
    </div>
  );
}
