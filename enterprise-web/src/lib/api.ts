import { clearAuth, getAccessToken, persistAuth } from "./auth";
import {
  DEMO_ANALYSIS,
  DEMO_APPEALS,
  DEMO_CALLS,
  DEMO_COACHING_PLANS,
  DEMO_DASHBOARD,
  DEMO_QA_QUEUE,
  DEMO_RULES,
  DEMO_USERS,
  demoLogin,
} from "./demo-data";
import type {
  AdminUser,
  AnalysisResult,
  Appeal,
  AuthTokens,
  CallSummary,
  CoachingPlan,
  ConversationDNA,
  DashboardOverview,
  Paginated,
  QaQueueItem,
  RulebookRule,
} from "./types";

const DEFAULT_API = "http://localhost:8000";

export function getApiBase(): string {
  return (
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || DEFAULT_API
  );
}

export class ApiClientError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.body = body;
  }
}

type RequestOpts = {
  method?: string;
  body?: unknown;
  auth?: boolean;
  query?: Record<string, string | number | undefined | null>;
};

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const base = getApiBase();
  const url = new URL(path.startsWith("http") ? path : `${base}${path}`);
  if (opts.query) {
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    }
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Request-Id":
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `req_${Date.now()}`,
  };

  if (opts.auth !== false) {
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(url.toString(), {
    method: opts.method || "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });

  if (res.status === 401) {
    clearAuth();
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const detail =
      typeof data === "object" &&
      data &&
      "detail" in data &&
      typeof (data as { detail: unknown }).detail === "string"
        ? (data as { detail: string }).detail
        : `HTTP ${res.status}`;
    throw new ApiClientError(detail, res.status, data);
  }

  return data as T;
}

async function withDemoFallback<T>(
  live: () => Promise<T>,
  demo: () => T
): Promise<{ data: T; source: "api" | "demo" }> {
  try {
    const data = await live();
    return { data, source: "api" };
  } catch {
    return { data: demo(), source: "demo" };
  }
}

/** Normalize backend scoring payload → AnalysisResult field names. */
function normalizeAnalysis(
  raw: AnalysisResult | Record<string, unknown>,
  callId: string
): AnalysisResult {
  const r = raw as Record<string, unknown>;
  const root = (r.root_cause || {}) as Record<string, unknown>;
  const coaching = (r.coaching || {}) as Record<string, unknown>;
  const leak = (r.revenue_leak || {}) as Record<string, unknown>;
  const metaIn = (r.meta || {}) as Record<string, unknown>;
  const tipsRaw = (coaching.tips || coaching.call_tips || []) as Record<
    string,
    unknown
  >[];

  return {
    ...(raw as AnalysisResult),
    meta: {
      call_id: String(metaIn.call_id || callId),
      tenant_id: String(metaIn.tenant_id || "default"),
      analyzed_at: String(metaIn.analyzed_at || new Date().toISOString()),
      pipeline_version: String(metaIn.pipeline_version || "1.0.0"),
      rulebook_version: String(metaIn.rulebook_version || "1.0.0"),
      status: (metaIn.status as AnalysisResult["meta"]["status"]) || "scored",
      trace_id: String(metaIn.trace_id || `trace_${callId}`),
      sop_id: metaIn.sop_id as string | undefined,
      industry_code: metaIn.industry_code as string | undefined,
    },
    score: (r.score as number | null) ?? null,
    stage_scores: (r.stage_scores as AnalysisResult["stage_scores"]) || {
      opening: null,
      discovery: null,
      pitch: null,
      objection: null,
      close: null,
      outro: null,
    },
    violations: (r.violations as AnalysisResult["violations"]) || [],
    evidence: (r.evidence as AnalysisResult["evidence"]) || [],
    root_cause: {
      primary_code:
        (root.primary_code as string | null) ??
        (root.primary_cause_code as string | null) ??
        null,
      label: (root.label as string | null) ?? null,
      confidence: (root.confidence as number | null) ?? null,
      contributing_factors:
        (root.contributing_factors as string[]) ||
        (root.contributing_factors as string[]) ||
        [],
      evidence_refs: (root.evidence_refs as string[]) || [],
      status:
        (root.status as AnalysisResult["root_cause"]["status"]) ||
        (root.verdict === "Insufficient Evidence"
          ? "Insufficient Evidence"
          : "ok"),
      reason:
        (root.reason as string | undefined) ||
        (root.explanation as string | undefined),
      children:
        (root.children as AnalysisResult["root_cause"]["children"]) ||
        undefined,
    },
    coaching: {
      priority:
        (coaching.priority as AnalysisResult["coaching"]["priority"]) || "medium",
      tips: tipsRaw.map((tip, idx) => ({
        tip_id: String(tip.tip_id || tip.cause_code || `tip_${idx}`),
        title: String(tip.title || "Coaching tip"),
        script_suggestion: String(
          tip.script_suggestion || tip.action_markdown || tip.explanation || ""
        ),
        linked_rule_ids: (tip.linked_rule_ids as string[]) ||
          (tip.rule_code ? [String(tip.rule_code)] : []),
        evidence_refs: (tip.evidence_refs as string[]) || [],
      })),
      drill_ids:
        (coaching.drill_ids as string[]) ||
        (coaching.drill_ids as string[]) ||
        [],
    },
    revenue_leak: {
      estimated_loss_vnd:
        (leak.estimated_loss_vnd as number | null) ??
        (leak.estimated_amount as number | null) ??
        null,
      leak_codes:
        (leak.leak_codes as string[]) ||
        (leak.leak_codes as string[]) ||
        [],
      probability: (leak.probability as number | null) ?? null,
      explanation: String(leak.explanation || ""),
      evidence_refs: (leak.evidence_refs as string[]) || [],
      status:
        (leak.status as AnalysisResult["revenue_leak"]["status"]) ||
        (leak.verdict === "Insufficient Evidence"
          ? "Insufficient Evidence"
          : "ok"),
    },
  };
}

export const api = {
  async login(input: {
    tenant_code: string;
    email: string;
    password: string;
  }): Promise<AuthTokens> {
    try {
      const tokens = await request<AuthTokens>("/v1/auth/login", {
        method: "POST",
        body: input,
        auth: false,
      });
      persistAuth(tokens);
      return tokens;
    } catch {
      const tokens = demoLogin(input.email, input.password);
      persistAuth(tokens);
      return tokens;
    }
  },

  async logout(): Promise<void> {
    try {
      await request("/v1/auth/logout", { method: "POST" });
    } catch {
      /* demo / offline */
    } finally {
      clearAuth();
    }
  },

  async getDashboardOverview(query?: {
    from?: string;
    to?: string;
  }): Promise<{ data: DashboardOverview; source: "api" | "demo" }> {
    return withDemoFallback(
      () =>
        request<DashboardOverview>("/v1/dashboard/overview", { query }),
      () => DEMO_DASHBOARD
    );
  },

  async listCalls(query?: {
    agent_id?: string;
    status?: string;
    cursor?: string;
    limit?: number;
  }): Promise<{ data: Paginated<CallSummary>; source: "api" | "demo" }> {
    return withDemoFallback(
      () =>
        request<Paginated<CallSummary>>("/v1/calls", {
          query: {
            agent_id: query?.agent_id,
            status: query?.status,
            cursor: query?.cursor,
            limit: query?.limit ?? 50,
          },
        }),
      () => ({ data: DEMO_CALLS, next_cursor: null, limit: 50 })
    );
  },

  async getCallAnalysis(
    callId: string
  ): Promise<{ data: AnalysisResult; source: "api" | "demo" }> {
    const res = await withDemoFallback(
      async () =>
        normalizeAnalysis(
          await request<AnalysisResult>(`/v1/calls/${callId}/analysis`),
          callId
        ),
      () =>
        normalizeAnalysis(
          {
            ...DEMO_ANALYSIS,
            meta: { ...DEMO_ANALYSIS.meta, call_id: callId },
          },
          callId
        )
    );
    return res;
  },

  async listRules(query?: {
    category?: string;
    q?: string;
  }): Promise<{ data: Paginated<RulebookRule>; source: "api" | "demo" }> {
    return withDemoFallback(
      () =>
        request<Paginated<RulebookRule>>("/v1/rulebook/rules", {
          query: { category: query?.category, q: query?.q, limit: 100 },
        }),
      () => {
        let rows = DEMO_RULES;
        if (query?.category) {
          rows = rows.filter((r) => r.category === query.category);
        }
        if (query?.q) {
          const q = query.q.toLowerCase();
          rows = rows.filter(
            (r) =>
              r.rule_code.toLowerCase().includes(q) ||
              r.title.toLowerCase().includes(q)
          );
        }
        return { data: rows, next_cursor: null, limit: 100 };
      }
    );
  },

  async listCoachingPlans(): Promise<{
    data: CoachingPlan[];
    source: "api" | "demo";
  }> {
    return withDemoFallback(
      async () => {
        const res = await request<{ data: CoachingPlan[] }>(
          "/v1/coaching/plans"
        );
        return res.data;
      },
      () => DEMO_COACHING_PLANS
    );
  },

  async listQaQueue(): Promise<{
    data: QaQueueItem[];
    source: "api" | "demo";
  }> {
    return withDemoFallback(
      async () => {
        const res = await request<{ data: QaQueueItem[] }>("/v1/qa/queue");
        return res.data;
      },
      () => DEMO_QA_QUEUE
    );
  },

  async listAppeals(): Promise<{ data: Appeal[]; source: "api" | "demo" }> {
    return withDemoFallback(
      async () => {
        const res = await request<{ data: Appeal[] }>("/v1/appeals");
        return res.data;
      },
      () => DEMO_APPEALS
    );
  },

  async listUsers(): Promise<{ data: AdminUser[]; source: "api" | "demo" }> {
    return withDemoFallback(
      async () => {
        const res = await request<{ data: AdminUser[] }>("/v1/admin/users");
        return res.data;
      },
      () => DEMO_USERS
    );
  },

  async getConversationDna(
    callId?: string
  ): Promise<{ data: ConversationDNA; source: "api" | "demo" }> {
    return withDemoFallback(
      () =>
        request<ConversationDNA>("/v1/analytics/conversation-dna", {
          query: { call_id: callId },
        }),
      () => DEMO_ANALYSIS.conversation_dna!
    );
  },

  async getRevenueLeakSummary(query?: {
    from?: string;
    to?: string;
  }): Promise<{
    data: {
      estimated_total: number;
      currency: string;
      insufficient_evidence_calls: number;
      top_components: {
        cause_code: string;
        amount: number;
        call_count: number;
      }[];
    };
    source: "api" | "demo";
  }> {
    return withDemoFallback(
      () =>
        request("/v1/revenue-leak/summary", {
          query,
        }),
      () => ({
        estimated_total: DEMO_DASHBOARD.estimated_revenue_leak_vnd,
        currency: "VND",
        insufficient_evidence_calls: DEMO_DASHBOARD.ie_leak_calls,
        top_components: DEMO_DASHBOARD.top_root_causes.map((c, i) => ({
          cause_code: c.cause_code,
          amount: [180000000, 142000000, 106500000][i] ?? 50000000,
          call_count: c.count,
        })),
      })
    );
  },
};
