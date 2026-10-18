// Mirrors models/knowledge/review_judgment.py; rows are the recorded attempt's judgments.
export interface JudgmentRequirement {
  task: { repository: string; path: string };
  packet: string;
  id: string;
  version: string;
}

export interface ReviewJudgmentRow {
  attempt: number;
  row_id: string;
  subject: string;
  disposition: string;
  effect?: string;
  revision?: number;
  reason: string;
  because: (string | JudgmentRequirement)[];
  covered_entries?: number;
  examined_members?: number;
  history_path: string;
  closed: boolean;
}

export interface ReviewProofDisplay {
  id: string;
  invariant: string;
  path: string;
  symbol?: string;
  facet: string;
  state: 'current' | 'stale' | 'unverifiable';
  detail: string;
}

export interface ReviewJudgmentPane {
  state: 'rows' | 'none' | 'unavailable';
  detail: string;
  rows: ReviewJudgmentRow[];
  proofs: ReviewProofDisplay[];
}
