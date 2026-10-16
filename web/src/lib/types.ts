// Row shapes for the tables and RPCs the web app reads. Kept by hand to
// match geo/models.py and alembic migrations 0001–0003.

export type Role = "admin" | "client";
export type MemberRole = "viewer" | "editor";
export type Relationship = "self" | "direct" | "indirect" | "ignored" | "pending";

export interface Client {
  id: number;
  name: string;
  domain: string;
  brand: string | null;
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

export const AI_APP_LABELS: Record<string, string> = {
  claude: "Claude",
  chatgpt: "ChatGPT",
  gemini: "Gemini",
  perplexity: "Perplexity",
  google_aio: "Google AI Overviews",
};

export const appLabel = (app: string) => AI_APP_LABELS[app] ?? app;
