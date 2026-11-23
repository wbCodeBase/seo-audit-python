// Row shapes for the tables and RPCs the web app reads. Kept by hand to
// match geo/models.py and alembic migrations 0001–0003.

export type Role = "admin" | "client";
// viewer: read · editor: + questions/competitors · manager: + run audits, see spend
export type MemberRole = "viewer" | "editor" | "manager";
export type Relationship = "self" | "direct" | "indirect" | "ignored" | "pending";

export interface Client {
  id: number;
  name: string;
  domain: string;
  brand: string | null;
}

export interface SpendRow {
  client_id: number;
  ai_app: string;
  source: Source;
  answers: number;
  cost_usd: number;
}

export interface Question {
  id: number;
  client_id: number;
  kind: "keyword" | "question";
  text: string;
  topic: string | null;
  funnel_stage: string | null;
  tags: string[];
  active: boolean;
  ai_apps: string[];
  source: "sheet" | "platform";
  frequency: "daily" | "weekly" | "monthly";
  country: string | null;
  created_at: string;
}

export interface Competitor {
  id: number;
  client_id: number;
  key: string;
  name: string;
  domain: string | null;
  relationship: Relationship;
  source: string;
  times_seen: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface MetricsRow {
  grp: string;
  answers: number;
  answers_naming_us: number;
  mention_rate: number | null;
  share_of_voice: number | null;
  position_score: number | null;
  citation_rate: number | null;
  coverage: number | null;
  sentiment: number | null;
  visibility_score: number | null;
  sentiment_pending: number;
  estimated: boolean;
}

export interface CompetitorSov {
  competitor_id: number;
  name: string;
  domain: string | null;
  relationship: Relationship;
  mentions: number;
  share_of_voice: number | null;
}

export interface MatrixCell {
  question_id: number;
  question: string;
  topic: string | null;
  ai_app: string;
  answered_at: string;
  answer_id: string;
  named: boolean;
  mention_position: number | null;
  rank: number | null;
  cited: boolean;
  estimated: boolean;
}

export interface Viewer {
  id: string;
  email: string;
  role: Role;
  clients: Client[];
  memberRoles: Record<number, MemberRole>;
}

/**
 * Mirrors geo/engines.py. "api" = we ask the vendor's developer API (cheap
 * proxy); "observed" = what a real user sees in the product, captured by the
 * data provider (the ground truth). `family` pairs the two for comparison.
 */
export type Source = "api" | "observed";
export interface EngineInfo { key: string; label: string; source: Source; family: string }

export const ENGINES: EngineInfo[] = [
  { key: "chatgpt_web", label: "ChatGPT · real", source: "observed", family: "chatgpt" },
  { key: "gemini_web", label: "Gemini · real", source: "observed", family: "gemini" },
  { key: "google_aio", label: "Google AI Overviews", source: "observed", family: "google" },
  { key: "google_ai_mode", label: "Google AI Mode", source: "observed", family: "google_ai_mode" },
  { key: "chatgpt", label: "ChatGPT · API", source: "api", family: "chatgpt" },
  { key: "claude", label: "Claude · API", source: "api", family: "claude" },
];

export const ENGINE_KEYS = ENGINES.map((e) => e.key);
export const engineInfo = (key: string) => ENGINES.find((e) => e.key === key);
export const sourceOf = (key: string): Source => engineInfo(key)?.source ?? "api";
export const appLabel = (app: string) => engineInfo(app)?.label ?? app;
export const SOURCE_LABEL: Record<Source, string> = { observed: "Real user view", api: "API estimate" };
