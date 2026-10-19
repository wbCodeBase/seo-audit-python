"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { appLabel } from "@/lib/types";

type Task = { question_id: number; question: string; app: string; repeat: number };
type TaskState = Task & { status: "waiting" | "running" | "named" | "missing" | "failed"; detail?: string };
type Phase = "idle" | "confirm" | "starting" | "running" | "finishing" | "done" | "error";

const CONCURRENCY = 4;

async function post(body: object) {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let json: Record<string, unknown> = {};
  try {
    json = await res.json();
  } catch {
    json = { ok: false, error: `Unexpected response (${res.status}).` };
  }
  return json;
}

/**
 * Admin "Run now": asks every active question of this client on each AI app,
 * a few at a time from the browser (each step is a short server call), shows
 * progress, lets failed ones be retried, then rebuilds the metrics.
 */
export function RunNow({ clientId, activeQuestions }: { clientId: number; activeQuestions: number }) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [tasks, setTasks] = useState<TaskState[]>([]);
  const [message, setMessage] = useState("");
  const runId = useRef<string | null>(null);
  const [hasRun, setHasRun] = useState(false);
  const [refreshing, startRefresh] = useTransition();

  const update = (i: number, patch: Partial<TaskState>) =>
    setTasks((prev) => prev.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  async function runTasks(indices: number[], list: TaskState[]) {
    let next = 0;
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, indices.length) }, async () => {
      while (next < indices.length) {
        const i = indices[next++];
        const t = list[i];
        update(i, { status: "running", detail: undefined });
        const r = await post({ action: "task", run_id: runId.current, question_id: t.question_id, app: t.app, repeat: t.repeat });
        if (!r.ok || r.error) {
          update(i, { status: "failed", detail: String(r.error ?? "Failed") });
        } else {
          const pos = r.mention_position ? ` · #${r.mention_position}` : "";
          update(i, { status: r.named ? "named" : "missing", detail: r.named ? `Named${pos}` : "Not named" });
        }
      }
    }));
  }

  async function finish() {
    setPhase("finishing");
    const r = await post({ action: "finish", run_id: runId.current });
    if (!r.ok) {
      setPhase("error");
      setMessage(String(r.error ?? "Couldn't update the dashboard."));
      return;
    }
    setPhase("done");
    const stats = r.stats as { answers?: number; errors?: number } | undefined;
    setMessage(`Saved ${stats?.answers ?? 0} answers${stats?.errors ? `, ${stats.errors} failed` : ""}.`);
    // Re-render the server components below with the new numbers.
    startRefresh(() => router.refresh());
  }

  async function start() {
    setPhase("starting");
    setMessage("Reading questions…");
    const r = await post({ action: "start", client_id: clientId });
    if (!r.ok) {
      setPhase("error");
      setMessage(String(r.error ?? "Couldn't start."));
      return;
    }
    if (!r.run_id) {
      setPhase("error");
      setMessage(String(r.message ?? "No active questions."));
      return;
    }
    runId.current = String(r.run_id);
    setHasRun(true);
    const list: TaskState[] = (r.tasks as Task[]).map((t) => ({ ...t, status: "waiting" }));
    setTasks(list);
    setPhase("running");
    setMessage("");
    await runTasks(list.map((_, i) => i), list);
    await finish();
  }

  async function retryFailed() {
    const failed = tasks.map((t, i) => (t.status === "failed" ? i : -1)).filter((i) => i >= 0);
    if (!failed.length) return;
    setPhase("running");
    await runTasks(failed, tasks);
    await finish();
  }

  const done = tasks.filter((t) => ["named", "missing", "failed"].includes(t.status)).length;
  const named = tasks.filter((t) => t.status === "named").length;
  const failedCount = tasks.filter((t) => t.status === "failed").length;
  const busy = phase === "starting" || phase === "running" || phase === "finishing";

  if (phase === "idle") {
    return (
      <button className="btn-primary" onClick={() => setPhase("confirm")} disabled={activeQuestions === 0}
        title={activeQuestions === 0 ? "Add or resume a question first" : undefined}>
        Run now
      </button>
    );
  }

  return (
    <div className="card w-full p-4" role="region" aria-label="Run now">
      {phase === "confirm" ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-2">
            Ask all <span className="font-medium text-ink">{activeQuestions}</span> active question
            {activeQuestions === 1 ? "" : "s"} on every AI app now? This uses API credit and takes a few minutes.
          </p>
          <div className="flex gap-2">
            <button className="btn" onClick={() => setPhase("idle")}>Cancel</button>
            <button className="btn-primary" onClick={start}>Start run</button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="font-medium">
              {phase === "starting" && "Starting…"}
              {phase === "running" && `Asking AI apps… ${done} of ${tasks.length}`}
              {phase === "finishing" && "Scoring sentiment and updating the dashboard…"}
              {phase === "done" && `Done — named in ${named} of ${tasks.length} answers`}
              {phase === "error" && "Couldn't complete the run"}
            </span>
            <div className="flex gap-2">
              {!busy && failedCount > 0 && hasRun && (
                <button className="btn" onClick={retryFailed}>Retry {failedCount} failed</button>
              )}
              {!busy && <button className="btn" onClick={() => { setPhase("idle"); setTasks([]); setMessage(""); setHasRun(false); }}>Close</button>}
            </div>
          </div>
          {tasks.length > 0 && (
            <div className="h-2 rounded-full bg-surface-2" role="progressbar" aria-valuemin={0}
              aria-valuemax={tasks.length} aria-valuenow={done}>
              <div className="h-2 rounded-full bg-accent transition-all" style={{ width: `${(done / tasks.length) * 100}%` }} />
            </div>
          )}
          {message && (
            <p className={`text-sm ${phase === "error" ? "text-critical-ink" : "text-ink-2"}`} aria-live="polite">
              {message}
              {phase === "done" && (refreshing ? " Updating the dashboard…" : " Dashboard updated.")}
            </p>
          )}
          {tasks.length > 0 && (
            <details open={phase !== "done"}>
              <summary className="cursor-pointer text-xs text-ink-2">Details</summary>
              <ul className="mt-2 max-h-64 space-y-1 overflow-auto text-xs">
                {tasks.map((t, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span aria-hidden className={`w-4 shrink-0 ${
                      t.status === "named" ? "text-good-ink" : t.status === "failed" || t.status === "missing" ? "text-critical-ink" : "text-muted"}`}>
                      {t.status === "named" ? "✓" : t.status === "missing" ? "✕" : t.status === "failed" ? "!" : t.status === "running" ? "…" : "·"}
                    </span>
                    <span className="w-16 shrink-0 text-muted">{appLabel(t.app)}</span>
                    <span className="min-w-0 flex-1 truncate">{t.question}</span>
                    <span className="shrink-0 text-muted">{t.detail ?? (t.status === "running" ? "asking…" : "")}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
