import Link from "next/link";
import { appLabel, type MatrixCell } from "@/lib/types";
import { dateTime } from "@/lib/format";

/**
 * Questions × AI apps, from each pair's most recent answer in the period.
 * Status colours always come with an icon and a word, never colour alone.
 */
export function GeoMatrix({ cells, apps, clientId }: { cells: MatrixCell[]; apps: string[]; clientId: number }) {
  if (cells.length === 0) return <p className="text-sm text-ink-2">No answers in this period.</p>;

  const questions = new Map<number, { text: string; topic: string | null }>();
  const byKey = new Map<string, MatrixCell>();
  for (const c of cells) {
    questions.set(c.question_id, { text: c.question, topic: c.topic });
    byKey.set(`${c.question_id}:${c.ai_app}`, c);
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px]">
        <thead>
          <tr>
            <th className="th">Question</th>
            {apps.map((a) => <th key={a} className="th w-36">{appLabel(a)}</th>)}
          </tr>
        </thead>
        <tbody>
          {[...questions.entries()].map(([qid, q]) => (
            <tr key={qid}>
              <td className="td">
                <div className="line-clamp-2">{q.text}</div>
                {q.topic && <span className="chip mt-1">{q.topic}</span>}
              </td>
              {apps.map((a) => {
                const c = byKey.get(`${qid}:${a}`);
                if (!c) return <td key={a} className="td text-muted">—</td>;
                const where = c.mention_position ? `#${c.mention_position} in answer` : c.rank ? `rank ${c.rank}` : "";
                return (
                  <td key={a} className="td">
                    <Link
                      href={`/c/${clientId}/answers?answer=${c.answer_id}`}
                      title={`Latest answer ${dateTime(c.answered_at)}${c.estimated ? " (imported history: named = cited)" : ""}`}
                      className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ${
                        c.named ? "bg-good-wash text-good-ink" : "bg-critical-wash text-critical-ink"
                      }`}
                    >
                      <span aria-hidden>{c.named ? "✓" : "✕"}</span>
                      <span>{c.named ? "Named" : "Missing"}</span>
                      {c.named && where && <span className="font-normal">· {where}</span>}
                    </Link>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-muted">Each cell is the latest answer in the period; click to read it.</p>
    </div>
  );
}
