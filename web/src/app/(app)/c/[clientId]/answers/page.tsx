import type { Metadata } from "next";
import Link from "next/link";
import { requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { dateTime } from "@/lib/format";
import { appLabel } from "@/lib/types";

export const metadata: Metadata = { title: "Answers" };

const PAGE_SIZE = 20;

type AnswerRow = {
  id: string;
  ai_app: string;
  model: string | null;
  repeat: number;
  question_id: number;
  question_text: string;
  answer_text: string | null;
  grounded: boolean;
  sentiment: number | null;
  created_at: string;
  mentions: {
    entity_type: string; entity_name: string; mentioned: boolean | null;
    mention_position: number | null; rank: number | null; cited: boolean;
  }[];
  citations: { position: number; url: string; domain: string; title: string | null }[];
};

const safeUrl = (u: string) => (/^https?:\/\//i.test(u) ? u : null);

export default async function AnswersPage(props: PageProps<"/c/[clientId]/answers">) {
  const { clientId } = await props.params;
  const { client } = await requireClient(clientId);
  const sp = await props.searchParams;
  const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : "");
  const answerId = one(sp.answer);
  const app = one(sp.app);
  const questionId = Number(one(sp.question)) || null;
  const page = Math.max(1, Number(one(sp.page)) || 1);

  const supabase = await createClient();
  let query = supabase
    .from("answers")
    .select(
      "id, ai_app, model, repeat, question_id, question_text, answer_text, grounded, sentiment, created_at, " +
        "mentions(entity_type, entity_name, mentioned, mention_position, rank, cited), " +
        "citations(position, url, domain, title)",
    )
    .eq("client_id", client.id)
    .is("error", null)
    .order("created_at", { ascending: false })
    .order("position", { referencedTable: "citations" })
    .range((page - 1) * PAGE_SIZE, page * PAGE_SIZE); // one extra row tells us if there's a next page
  if (answerId) query = query.eq("id", answerId);
  if (app) query = query.eq("ai_app", app);
  if (questionId) query = query.eq("question_id", questionId);

  const [{ data, error }, { data: questions }] = await Promise.all([
    query,
    supabase.from("questions").select("id, text").eq("client_id", client.id).order("text"),
  ]);
  const rows = ((data ?? []) as unknown as AnswerRow[]);
  const hasNext = rows.length > PAGE_SIZE;
  const answers = rows.slice(0, PAGE_SIZE);

  const qs = (patch: Record<string, string | number | null>) => {
    const p = new URLSearchParams();
    const base = { app, question: questionId ?? "", page: "", ...patch } as Record<string, string | number | null>;
    for (const [k, v] of Object.entries(base)) if (v) p.set(k, String(v));
    const s = p.toString();
    return `/c/${client.id}/answers${s ? "?" + s : ""}`;
  };

  return (
    <div className="space-y-4">
      <form className="flex flex-wrap items-end gap-2" action={`/c/${client.id}/answers`}>
        <div>
          <label className="label" htmlFor="a-app">AI app</label>
          <select id="a-app" name="app" defaultValue={app} className="input w-auto">
            <option value="">All</option>
            <option value="claude">Claude</option>
            <option value="chatgpt">ChatGPT</option>
          </select>
        </div>
        <div className="min-w-64 flex-1">
          <label className="label" htmlFor="a-q">Question</label>
          <select id="a-q" name="question" defaultValue={questionId ?? ""} className="input">
            <option value="">All questions</option>
            {(questions ?? []).map((q) => <option key={q.id} value={q.id}>{q.text}</option>)}
          </select>
        </div>
        <button className="btn">Filter</button>
        {answerId && <Link href={qs({})} className="btn">Show all answers</Link>}
      </form>

      {error && <p className="rounded-lg bg-critical-wash px-3 py-2 text-sm text-critical-ink">{error.message}</p>}
      {answers.length === 0 && !error && <p className="text-sm text-ink-2">No answers match.</p>}

      {answers.map((a) => {
        const ours = a.mentions.filter((m) => m.entity_type !== "competitor");
        const named = ours.some((m) => m.mentioned ?? m.cited);
        const pos = Math.min(...ours.map((m) => m.mention_position ?? Infinity));
        return (
          <article key={a.id} className="card p-4">
            <header className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h2 className="font-medium">{a.question_text}</h2>
                <p className="text-xs text-muted">
                  {appLabel(a.ai_app)}{a.model ? ` · ${a.model}` : ""} · {dateTime(a.created_at)}
                  {a.repeat > 1 ? ` · repeat ${a.repeat}` : ""}{a.grounded ? "" : " · answered without web search"}
                </p>
              </div>
              <div className="flex flex-wrap gap-1">
                <span className={`chip ${named ? "border-transparent bg-good-wash text-good-ink" : "border-transparent bg-critical-wash text-critical-ink"}`}>
                  {named ? `✓ ${client.name} named${Number.isFinite(pos) ? ` #${pos}` : ""}` : `✕ ${client.name} not named`}
                </span>
                {a.sentiment != null && <span className="chip">Sentiment {a.sentiment}/100</span>}
              </div>
            </header>

            {a.answer_text ? (
              <details className="mt-3" open={Boolean(answerId)}>
                <summary className="cursor-pointer text-sm text-ink-2">Full answer</summary>
                <div className="mt-2 whitespace-pre-wrap rounded-lg bg-surface-2 p-3 text-sm leading-relaxed">{a.answer_text}</div>
              </details>
            ) : (
              <p className="mt-3 text-sm text-muted">Answer text wasn&apos;t saved for this run (imported from the sheet).</p>
            )}

            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="mb-1 text-xs font-medium text-muted">Brands</h3>
                <table className="w-full">
                  <thead>
                    <tr><th className="th pl-0">Brand</th><th className="th">Named</th><th className="th">Search rank</th><th className="th">Cited</th></tr>
                  </thead>
                  <tbody>
                    {a.mentions.map((m, i) => (
                      <tr key={i}>
                        <td className="td pl-0">{m.entity_name}{m.entity_type !== "competitor" && <span className="text-muted"> (you)</span>}</td>
                        <td className="td">{m.mentioned == null ? "?" : m.mentioned ? `Yes${m.mention_position ? ` #${m.mention_position}` : ""}` : "No"}</td>
                        <td className="td tabular-nums">{m.rank ?? "—"}</td>
                        <td className="td">{m.cited ? "Yes" : "No"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-medium text-muted">Cited sources ({a.citations.length})</h3>
                {a.citations.length === 0 ? (
                  <p className="text-sm text-muted">None recorded.</p>
                ) : (
                  <ol className="space-y-1 text-sm">
                    {a.citations.map((c) => {
                      const href = safeUrl(c.url);
                      return (
                        <li key={c.position} className="flex gap-2">
                          <span className="w-5 shrink-0 text-right tabular-nums text-muted">{c.position}.</span>
                          {href ? (
                            <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="truncate hover:underline">
                              {c.title || c.domain}
                            </a>
                          ) : (
                            <span className="truncate">{c.title || c.domain}</span>
                          )}
                          <span className="shrink-0 text-muted">{c.domain}</span>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>
            </div>
          </article>
        );
      })}

      {(page > 1 || hasNext) && !answerId && (
        <nav className="flex justify-between" aria-label="Pages">
          {page > 1 ? <Link className="btn" href={qs({ page: page - 1 })}>← Newer</Link> : <span />}
          {hasNext && <Link className="btn" href={qs({ page: page + 1 })}>Older →</Link>}
        </nav>
      )}
    </div>
  );
}
