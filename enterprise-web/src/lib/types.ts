/** Canonical AnalysisResult + enterprise API types (AQATE v1). */

export type Verdict =
  | "pass"
  | "fail"
  | "not_applicable"
  | "Insufficient Evidence";

export type IeStatus = "ok" | "Insufficient Evidence" | "partial";

export type Role =
  | "agent"
  | "team_lead"
  | "qa_specialist"
  | "qa_manager"
  | "admin"
  | "executive";

export interface User {
  id: string;
  email: string;
  full_name: string;
  roles: Role[];
  permissions: string[];
  team_id?: string;
  team_name?: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
}

export interface EvidenceSpan {
  evidence_id: string;
  type: "transcript_span" | "prosody" | "silence" | "interrupt";
  speaker: "agent" | "customer" | "unknown";
  quote: string;
  audio_ts_start: number;
  audio_ts_end: number;
  turn_index: number;
  confidence: number;
  feature_keys?: string[];
  stage?: string;
}

export interface Violation {
  rule_id: string;
  severity: "critical" | "major" | "minor" | "info";
  status: Verdict;
  message: string;
  evidence_refs: string[];
  deduction: number;
}

export interface RootCause {
  primary_code: string | null;
  label: string | null;
  confidence: number | null;
  contributing_factors: string[];
  evidence_refs: string[];
  status: IeStatus;
  reason?: string;
  children?: RootCauseNode[];
}

export interface RootCauseNode {
  code: string;
  label: string;
  weight: number;
  children?: RootCauseNode[];
}

export interface CoachingTip {
  tip_id: string;
  title: string;
  script_suggestion: string;
  linked_rule_ids: string[];
  evidence_refs: string[];
}

export interface Coaching {
  priority: "high" | "medium" | "low";
  tips: CoachingTip[];
  drill_ids: string[];
}

export interface RevenueLeak {
  estimated_loss_vnd: number | null;
  leak_codes: string[];
  probability: number | null;
  explanation: string;
  evidence_refs: string[];
  status: IeStatus;
}

export type StageKey =
  | "opening"
  | "discovery"
  | "pitch"
  | "objection"
  | "close"
  | "outro";

export type StageScores = Record<StageKey, number | null>;

export interface EmotionPoint {
  t_sec: number;
  progress_pct: number;
  valence: number;
  arousal: number;
  label: string;
  speaker: "agent" | "customer";
}

export interface ConversationDNA {
  dimensions: {
    key: string;
    label: string;
    score: number;
  }[];
  summary: string;
}

export interface AnalysisResult {
  meta: {
    call_id: string;
    tenant_id: string;
    analyzed_at: string;
    pipeline_version: string;
    rulebook_version: string;
    sop_id?: string;
    industry_code?: string;
    status: "scored" | "partial" | "failed" | "pending";
    trace_id: string;
  };
  score: number | null;
  stage_scores: StageScores;
  violations: Violation[];
  evidence: EvidenceSpan[];
  root_cause: RootCause;
  coaching: Coaching;
  revenue_leak: RevenueLeak;
  emotion_timeline?: EmotionPoint[];
  conversation_dna?: ConversationDNA;
  vcie?: Record<string, unknown>;
}

export type CallStatus =
  | "received"
  | "queued"
  | "processing"
  | "scored"
  | "failed"
  | "needs_review";

export interface CallSummary {
  id: string;
  external_call_id: string;
  agent_user_id: string;
  agent_name: string;
  campaign_code: string;
  status: CallStatus;
  started_at: string;
  duration_sec: number;
  crm_outcome: string | null;
  overall_score: number | null;
  result: "pass" | "fail" | "needs_review" | "Insufficient Evidence" | null;
  customer_phone_masked: string;
}

export interface Paginated<T> {
  data: T[];
  next_cursor: string | null;
  limit: number;
}

export interface DashboardOverview {
  window: { from: string; to: string };
  calls_total: number;
  scored: number;
  insufficient_evidence: number;
  failed: number;
  avg_score: number;
  auto_fail_rate: number;
  estimated_revenue_leak_vnd: number;
  ie_leak_calls: number;
  top_failed_rules: { rule_code: string; title: string; fail_count: number }[];
  top_root_causes: { cause_code: string; label: string; count: number }[];
  employee_scores: {
    user_id: string;
    full_name: string;
    team_name: string;
    avg_score: number;
    calls: number;
    leak_vnd: number;
    ie_rate: number;
  }[];
  kpis: {
    key: string;
    label: string;
    value: number;
    unit: string;
    delta_pct: number;
  }[];
  stage_heatmap: {
    agent_name: string;
    stages: Partial<Record<StageKey, number | null>>;
  }[];
  funnel: {
    stage: string;
    label: string;
    count: number;
    rate: number;
  }[];
  pareto: {
    cause_code: string;
    label: string;
    count: number;
    share: number;
    cumulative_share: number;
  }[];
  emotion_timeline: {
    progress_pct: number;
    avg_valence: number;
    samples: number;
    label: string;
  }[];
  coaching_highlights: {
    plan_id: string;
    agent_user_id: string | null;
    title: string;
    priority: string;
    status: string;
  }[];
}

export interface RulebookRule {
  rule_code: string;
  category: string;
  title: string;
  description: string;
  severity: "critical" | "major" | "minor" | "info";
  weight: number;
  auto_fail: boolean;
  status: "active" | "draft" | "deprecated";
  evaluator_type: string;
  version: number;
}

export interface CoachingPlan {
  id: string;
  agent_user_id: string;
  agent_name: string;
  period_start: string;
  period_end: string;
  status: "open" | "in_progress" | "completed";
  focus_areas: string[];
  items: {
    id: string;
    title: string;
    tip_id: string;
    status: "open" | "done" | "dismissed";
    drill_id?: string;
    script: string;
  }[];
  roleplay_scenarios: {
    id: string;
    title: string;
    objection: string;
    expected_script: string;
    difficulty: "easy" | "medium" | "hard";
  }[];
}

export interface QaQueueItem {
  call_id: string;
  agent_name: string;
  queue: "calibration" | "low_confidence" | "compliance" | "appeals" | "ie_heavy";
  score: number | null;
  confidence: number;
  flagged_at: string;
  reason: string;
}

export interface Appeal {
  id: string;
  call_id: string;
  agent_name: string;
  rule_code: string;
  rule_title: string;
  current_verdict: Verdict;
  proposed_verdict: Verdict;
  reason_code: string;
  reason_text: string;
  status: "open" | "under_review" | "overturned" | "upheld";
  created_at: string;
  evidence_quote?: string;
}

export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  roles: Role[];
  team_name: string;
  active: boolean;
  last_login_at: string | null;
}

export interface ApiError {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  request_id?: string;
}
