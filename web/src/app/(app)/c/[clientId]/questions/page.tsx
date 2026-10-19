import type { Metadata } from "next";
import { requireClient } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { appLabel, type Question } from "@/lib/types";
import { ActionForm } from "@/components/action-form";
import { ActionButton } from "@/components/row-actions";
import { addQuestion, deleteQuestion, setQuestionActive, updateQuestion } from "./actions";

export const metadata: Metadata = { title: "Questions" };

export default async function QuestionsPage(props: PageProps<"/c/[clientId]/questions">) {
  const { clientId } = await props.params;
  const { client, editable } = await requireClient(clientId);
  const supabase = await createClient();
  const { data } = await supabase
    .from("questions")
    .select("id, client_id, kind, text, topic, funnel_stage, tags, active, ai_apps, source, created_at")
    .eq("client_id", client.id)
    .order("active", { ascending: false })
    .order("created_at", { ascending: false });
  const questions = (data ?? []) as Question[];

  return (
    <div className="space-y-4">
      {editable && (
        <section className="card p-4">
          <h2 className="font-medium">Add a buyer question</h2>
          <p className="mb-3 text-sm text-ink-2">
            Write it the way a real buyer would ask an AI app. It&apos;s asked every day from the next run.
          </p>
          <ActionForm action={addQuestion.bind(null, client.id)} resetOnSuccess className="grid gap-3 md:grid-cols-6">
            <div className="md:col-span-6">
              <label className="label" htmlFor="text">Question</label>
              <input id="text" name="text" required maxLength={500} className="input"
                placeholder="Who are the most reliable NetSuite partners for a mid-size manufacturer in India?" />
            </div>
            <div className="md:col-span-2">
              <label className="label" htmlFor="topic">Topic</label>
              <input id="topic" name="topic" className="input" placeholder="NetSuite" />
            </div>
            <div>
              <label className="label" htmlFor="funnel_stage">Funnel stage</label>
              <select id="funnel_stage" name="funnel_stage" className="input">
                <option value="">—</option>
                <option value="TOFU">TOFU · awareness</option>
                <option value="MOFU">MOFU · consideration</option>
                <option value="BOFU">BOFU · decision</option>
              </select>
            </div>
            <div className="md:col-span-2">
              <label className="label" htmlFor="tags">Tags (comma-separated)</label>
              <input id="tags" name="tags" className="input" placeholder="partners, india" />
            </div>
            <fieldset className="flex flex-col justify-end">
              <legend className="label">AI apps</legend>
              <div className="flex gap-3 text-sm">
                {["claude", "chatgpt"].map((a) => (
                  <label key={a} className="flex items-center gap-1.5">
                    <input type="checkbox" name="ai_apps" value={a} defaultChecked /> {appLabel(a)}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="md:col-span-6">
              <button className="btn-primary">Add question</button>
            </div>
          </ActionForm>
        </section>
      )}

      <section className="card overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead>
            <tr>
              <th className="th">Question</th>
              <th className="th">Topic · stage</th>
              <th className="th">AI apps</th>
              <th className="th">Status</th>
              {editable && <th className="th"><span className="sr-only">Actions</span></th>}
            </tr>
          </thead>
          <tbody>
            {questions.map((q) => (
              <tr key={q.id} className={q.active ? "" : "text-muted"}>
                <td className="td">
                  <div>{q.text}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <span className="chip">{q.kind === "keyword" ? "Keyword" : "Question"}</span>
                    {q.source === "sheet" && <span className="chip" title="Managed in the Google Sheet">From sheet</span>}
                    {q.tags.map((t) => <span key={t} className="chip">{t}</span>)}
                  </div>
                </td>
                <td className="td">{[q.topic, q.funnel_stage].filter(Boolean).join(" · ") || "—"}</td>
                <td className="td">{q.ai_apps.length ? q.ai_apps.map(appLabel).join(", ") : "All"}</td>
                <td className="td">{q.active ? "Active" : "Paused"}</td>
                {editable && (
                  <td className="td">
                    {q.source === "platform" ? (
                      <div className="space-y-2">
                        <div className="flex gap-2">
                          <ActionButton label={q.active ? "Pause" : "Resume"}
                            action={setQuestionActive.bind(null, client.id, q.id, !q.active)} />
                          <ActionButton label="Delete" confirm="Delete this question and all its past answers?"
                            action={deleteQuestion.bind(null, client.id, q.id)} />
                        </div>
                        <details>
                          <summary className="cursor-pointer text-xs text-ink-2">Edit</summary>
                          <ActionForm action={updateQuestion.bind(null, client.id, q.id)} className="mt-2 grid w-72 gap-2">
                            <textarea name="text" required maxLength={500} rows={3} defaultValue={q.text}
                              className="input" aria-label="Question" />
                            <input name="topic" defaultValue={q.topic ?? ""} placeholder="Topic" className="input" aria-label="Topic" />
                            <select name="funnel_stage" defaultValue={q.funnel_stage ?? ""} className="input" aria-label="Funnel stage">
                              <option value="">—</option>
                              <option value="TOFU">TOFU · awareness</option>
                              <option value="MOFU">MOFU · consideration</option>
                              <option value="BOFU">BOFU · decision</option>
                            </select>
                            <input name="tags" defaultValue={q.tags.join(", ")} placeholder="Tags" className="input" aria-label="Tags" />
                            <div className="flex gap-3 text-xs">
                              {["claude", "chatgpt"].map((a) => (
                                <label key={a} className="flex items-center gap-1">
                                  <input type="checkbox" name="ai_apps" value={a}
                                    defaultChecked={q.ai_apps.length === 0 || q.ai_apps.includes(a)} /> {appLabel(a)}
                                </label>
                              ))}
                            </div>
                            <button className="btn">Save changes</button>
                          </ActionForm>
                        </details>
                      </div>
                    ) : (
                      <span className="text-xs text-muted">Edit in the sheet</span>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {questions.length === 0 && (
              <tr><td className="td text-ink-2" colSpan={5}>No questions yet.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
