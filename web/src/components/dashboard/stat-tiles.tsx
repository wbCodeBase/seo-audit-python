import { pct, score } from "@/lib/format";
import type { MetricsRow } from "@/lib/types";

const HELP = {
  visibility: "0.40 × mention rate + 0.25 × position + 0.20 × share of voice + 0.15 × sentiment",
  mention: "Answers naming you ÷ answers naming you or a direct competitor",
  sov: "Your mentions ÷ (yours + direct competitors’)",
  position: "Average of the rank score (100 = first result, −12 per place, +10 if cited)",
  coverage: "Question × AI app pairs where you were named at least once",
};

function Tile({
  label, value, help, note, primary = false,
}: { label: string; value: string; help: string; note?: string; primary?: boolean }) {
  return (
    <div className={`card p-4 ${primary ? "border-accent" : ""}`} title={help}>
      <div className="text-sm text-ink-2">{label}</div>
      <div className="mt-1 text-3xl font-semibold tracking-tight">{value}</div>
      <div className="mt-1 min-h-4 text-xs text-muted">{note ?? ""}</div>
    </div>
  );
}

export function StatTiles({ total }: { total: MetricsRow | null }) {
  const t = total;
  const visibilityNote =
    !t || t.answers === 0
      ? "No answers in this period"
      : t.visibility_score == null && t.sentiment_pending > 0
        ? "Waiting for sentiment scoring"
        : t.sentiment == null
          ? "Without sentiment (not scorable)"
          : `Sentiment ${score(t.sentiment)}/100`;

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      <Tile label="Visibility score" value={score(t?.visibility_score)} help={HELP.visibility} note={visibilityNote} primary />
      <Tile label="Mention rate" value={pct(t?.mention_rate)} help={HELP.mention}
        note={t ? `${t.answers_naming_us} of ${t.answers} answers name you` : undefined} />
      <Tile label="Share of voice" value={pct(t?.share_of_voice)} help={HELP.sov} />
      <Tile label="Position score" value={score(t?.position_score)} help={HELP.position} />
      <Tile label="Coverage" value={pct(t?.coverage)} help={HELP.coverage}
        note={t?.citation_rate != null ? `Cited in ${pct(t.citation_rate)} of answers` : undefined} />
    </div>
  );
}
