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

  async liveAssistantSuggest(turns: Record<string, unknown>[], nowTs?: number) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/live-assistant/suggest", {
          method: "POST",
          body: { turns, now_ts: nowTs },
        }),
      () => ({
        status: "ok",
        next_best_question: "Anh/chị đang quan tâm điều gì nhất ạ?",
        next_best_response: "Em hiểu. Em đề xuất phương án phù hợp nhu cầu.",
        alerts: [{ type: "buying_signal", severity: "high" }],
        closing_opportunity: true,
        latency_ms: 12,
        sla_ok: true,
      })
    );
  },

  
  async analyzePragmatics(turns: Record<string, unknown>[], dialectHint?: string) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/pragmatics/analyze", {
          method: "POST",
          body: { turns, dialect_hint: dialectHint },
        }),
      () => ({
        status: "ok",
        dialect: dialectHint || "south",
        explanation: "Demo pragmatics timeline",
        summary: { top_intent: "delay", avg_buying_probability: 0.28, avg_exit_risk: 0.22 },
        intents: ["delay", "decision_maker_missing"],
        objections: ["decision_maker_missing"],
        timeline: turns
          .filter((t) => String(t.speaker || "").includes("customer") || String(t.speaker || "") === "customer")
          .map((t, i) => ({
            turn_index: i,
            text: t.text,
            top_intent: "delay",
            hidden_meaning: ["Cần thêm ngữ cảnh trước khi kết luận."],
          })),
        intent_evolution: [{ turn_index: 0, intent: "delay", probability: 0.42 }],
        emotion_evolution: [{ turn_index: 0, emotion: "hesitant", probability: 0.4 }],
        turns: [
          {
            turn_index: 0,
            text: "Để em coi đã",
            intent_probability: { delay: 0.42, need_information: 0.22, soft_rejection: 0.18 },
            emotion_probability: { hesitant: 0.4, neutral: 0.4 },
            hidden_meaning: ["Muốn có thời gian xem lại; chưa phải từ chối cứng."],
            buying_probability: 0.28,
            exit_risk: 0.32,
            confidence: 0.42,
            evidence_quote: "Để em coi đã",
            status: "ok",
          },
        ],
      })
    );
  },

  async analyzePersonality(turns: Record<string, unknown>[]) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/personality/analyze", {
          method: "POST",
          body: { turns },
        }),
      () => ({
        status: "ok",
        disc: "C",
        buyer_type: "technical",
        personality_card: {
          style: "Chi tiết, cần số liệu",
          suggested_script: "Em gửi bảng so sánh thông số và bảo hành ạ.",
          forbidden_script: "Chốt luôn đi anh/chị!",
        },
      })
    );
  },

  async buildMemoryGraph(payload: Record<string, unknown>) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/memory-graph/build", {
          method: "POST",
          body: {
            call_id: payload.call_id ?? payload.callId ?? "demo-call",
            violations: payload.violations ?? [],
            root_cause: payload.root_cause ?? {},
            coaching: payload.coaching ?? {},
            intents: payload.intents ?? [],
            objections: payload.objections ?? [],
            products: payload.products ?? [],
          },
        }),
      () => ({
        status: "ok",
        nodes: [
          { id: "call:demo", type: "call", label: "Demo call" },
          { id: "rulebook:R1", type: "rulebook", label: "R1" },
          { id: "product:PKG-HEALTH", type: "product", label: "PKG-HEALTH" },
        ],
        edges: [
          { source: "call:demo", target: "rulebook:R1", relation: "call_violates_rule" },
          { source: "call:demo", target: "product:PKG-HEALTH", relation: "call_discusses_product" },
        ],
        stats: { node_count: 3, edge_count: 2, types: ["call", "product", "rulebook"] },
      })
    );
  },

  async exploreMemoryGraph(limit = 200) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/memory-graph/explore", {
          method: "POST",
          body: { limit },
        }),
      () => ({
        nodes: [
          { id: "product:the_tin_dung", type: "product", label: "Thẻ tín dụng" },
          { id: "objection:dat", type: "objection", label: "Phản đối giá" },
          { id: "sop:mo_dau", type: "sop", label: "SOP mở đầu" },
        ],
        edges: [
          {
            source: "product:the_tin_dung",
            target: "objection:dat",
            relation: "product_has_objection",
          },
          {
            source: "product:the_tin_dung",
            target: "sop:mo_dau",
            relation: "product_has_sop",
          },
        ],
        stats: { node_count: 3, edge_count: 2 },
        ok: true,
      })
    );
  },

  async searchKnowledge(query: string, nodeType?: string) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/memory-graph/knowledge/search", {
          method: "POST",
          body: { query, node_type: nodeType, limit: 20 },
        }),
      () => ({
        matches: [
          {
            id: "pricing:the_tin_dung_base",
            type: "pricing",
            label: "Giá thẻ tín dụng",
            content: "Phí thường niên 500.000đ",
            source: "pricing/card",
          },
        ],
        count: 1,
      })
    );
  },

  async askMemoryRag(question: string) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/memory-graph/ask", {
          method: "POST",
          body: { question, limit: 6 },
        }),
      () => ({
        status: "ok",
        answer:
          "Dựa trên dữ liệu Memory Graph:\n[1] Thẻ tín dụng: phí thường niên 500.000đ",
        citations: [
          {
            id: "pricing:the_tin_dung_base",
            source: "pricing/card",
            node_type: "pricing",
            score: 0.91,
          },
        ],
        evidence: [],
      })
    );
  },

  async memoryVersionHistory(entityId: string) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/memory-graph/versions", {
          method: "POST",
          body: { entity_id: entityId, limit: 50 },
        }),
      () => ({
        history: [
          {
            event: "node_upsert",
            timestamp: Date.now() / 1000,
            payload: { id: entityId, version: "1.0.0" },
          },
        ],
        count: 1,
      })
    );
  },

  async syncMemoryKnowledge(kind?: string) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/memory-graph/sync", {
          method: "POST",
          body: { kind: kind ?? null, reset: false },
        }),
      () => ({ ok: true, synced_nodes: 28, synced_edges: 22 })
    );
  },

  async listSimulatorScenarios(group?: string) {
    return withDemoFallback(
      () =>
        request<{ data: Record<string, unknown>[] }>("/v1/simulator/scenarios", {
          query: { group },
        }),
      () => ({
        data: [
          { id: "price_too_high", group: "price", prompt: "Giá đắt quá" },
          { id: "need_think", group: "delay", prompt: "Để em suy nghĩ đã" },
        ],
      })
    );
  },

  async startSimulator(group?: string, seed?: number) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/simulator/start", {
          method: "POST",
          body: { group, seed },
        }),
      () => ({
        status: "ok",
        scenario: { id: "price_too_high", customer_line: "Giá bên này hơi cao." },
        instruction: "Hãy xử lý phản đối giá bằng value reframe.",
      })
    );
  },

  async gradeSimulator(scenarioId: string, agentReply: string) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/simulator/grade", {
          method: "POST",
          body: { scenario_id: scenarioId, agent_reply: agentReply },
        }),
      () => ({
        status: "ok",
        score: 78,
        feedback: "Có empathy nhưng thiếu close ask.",
      })
    );
  },

  async scanFraud(turns: Record<string, unknown>[]) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/fraud/scan", {
          method: "POST",
          body: { turns },
        }),
      () => ({
        status: "ok",
        findings: [],
        risk_level: "low",
      })
    );
  },

  async generateAutoSop(
    goldenCalls: Record<string, unknown>[],
    version?: string
  ) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/auto-sop/generate", {
          method: "POST",
          body: { golden_calls: goldenCalls, version },
        }),
      () => ({
        status: "ok",
        version: version || "v1",
        sop: { title: "SOP Demo", steps: ["Mở đầu", "Khám phá", "Chốt"] },
        checklist: ["Xác nhận nhu cầu", "Xử lý giá"],
      })
    );
  },

  async forecastKpi(
    historical: Record<string, unknown>[],
    horizonDays = 30
  ) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/forecast/kpi", {
          method: "POST",
          body: { historical, horizon_days: horizonDays },
        }),
      () => ({
        status: "ok",
        projected_conversion_rate: 0.24,
        projected_revenue: 125000000,
        trend: 0.02,
        confidence: 0.7,
      })
    );
  },

  async salesOsDashboard(role = "CEO", extras: Record<string, unknown> = {}) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/sales-os/dashboard", {
          method: "POST",
          body: { role, extras },
        }),
      () => ({
        status: "ok",
        role,
        widget_names: [
          "Revenue Forecast",
          "Lead Health",
          "Revenue Leak",
          "Conversion Funnel",
          "Personality Distribution",
          "Coaching Progress",
          "Team Ranking",
          "AI Confidence",
          "Golden Call Gap",
          "Repeat Mistake",
        ],
        widgets: {
          "Revenue Forecast": { weekly: 25000000, monthly: 100000000, quarterly: 300000000, close_rate: 0.22, pipeline_risk: 0.28, confidence: 0.74 },
          "Lead Health": { avg_lead_score: 72, assigned: 12, unassigned: 0, stale: 2 },
          "Revenue Leak": { leak_amount: 8500000, top_causes: ["missed_callback", "weak_close"] },
          "Conversion Funnel": { leads: 1000, contacted: 700, qualified: 320, proposal: 180, won: 90 },
          "Personality Distribution": { consultative: 0.34, assertive: 0.28, empathic: 0.22, analytical: 0.16 },
          "Coaching Progress": { plans_open: 4, completed: 11, avg_improvement: 0.08 },
          "Team Ranking": [{ agent_id: "A1", close_rate: 0.31 }],
          "AI Confidence": { routing: 0.78, nba: 0.74, forecast: 0.7 },
          "Golden Call Gap": { gap_score: 0.22, top_gaps: ["discovery_depth", "value_stack"] },
          "Repeat Mistake": { top_mistakes: [{ code: "RM-OBJ-01", count: 12 }] },
        },
      })
    );
  },

  async salesOsRoute(
    lead: Record<string, unknown>,
    agents: Record<string, unknown>[],
    hour?: number
  ) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/sales-os/route", {
          method: "POST",
          body: { lead, agents, hour },
        }),
      () => ({
        lead_id: lead.lead_id,
        agent_id: "A1",
        lead_score: 78,
        assignment_reason: "Assigned to An (A1): skill/dna/close/industry/peak match",
        success_probability: 0.81,
        evidence: [{ factor: "telesale_skill", value: 0.92 }],
      })
    );
  },

  async salesOsNextBestAction(call: Record<string, unknown>, automate = false) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/sales-os/next-best-action", {
          method: "POST",
          body: { call, automate },
        }),
      () => ({
        action: "callback",
        confidence: 0.86,
        evidence: [{ field: "buy_signals", value: 3 }],
        expected_impact: "Timely callback captures warm intent.",
        schedule_at: "+2h",
        automation_jobs: automate ? [{ job_type: "callback_reminder", status: "completed" }] : [],
      })
    );
  },

  async salesOsForecast(
    historical: Record<string, unknown>[],
    pipeline: Record<string, unknown>[] = [],
    horizonDays = 30
  ) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/sales-os/forecast", {
          method: "POST",
          body: { historical, pipeline, horizon_days: horizonDays },
        }),
      () => ({
        status: "ok",
        close_rate: 0.22,
        weekly_revenue: 27500000,
        monthly_revenue: 110000000,
        quarterly_revenue: 330000000,
        pipeline_risk: 0.31,
        confidence: 0.76,
      })
    );
  },

  async salesOsSyncAll(payload: Record<string, unknown> = {}) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/sales-os/sync-all", {
          method: "POST",
          body: { payload },
        }),
      () => ({
        ok: true,
        results: [
          { connector: "hubspot", ok: true, records_in: 5, records_out: 5 },
          { connector: "twilio", ok: true, records_in: 5, records_out: 5 },
        ],
      })
    );
  },

  async salesOsQuality() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/sales-os/quality"),
      () => ({
        crm_sync_ok: true,
        no_data_loss: true,
        connectors: 13,
        audit_entries: 12,
        automation_jobs: 4,
        routing_decisions: 3,
      })
    );
  },

  async suggestMultiProduct(
    turns: Record<string, unknown>[],
    currentSku?: string
  ) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/multi-product/suggest", {
          method: "POST",
          body: { turns, current_sku: currentSku },
        }),
      () => ({
        status: "ok",
        suggestions: [
          { sku: "PKG-PLUS", action: "upsell", reason: "Quan tâm bảo hành" },
        ],
      })
    );
  },

  async selfLearningDashboard() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/self-learning/dashboard"),
      () => ({
        status: "ok",
        widgets: {
          new_patterns: 12,
          new_intents: 4,
          new_objections: 6,
          qa_queue: 8,
          approved_rules: 3,
          rejected_rules: 1,
          learning_velocity: 1.5,
          revenue_impact: 18000000,
          confidence_trend: 0.84,
          knowledge_growth: 27,
        },
        layers: {
          layer_1_raw_calls: 120,
          layer_2_verified_knowledge: 40,
          layer_3_approved_rules: 12,
          layer_4_production_knowledge: 9,
        },
        auto_apply_blocked: true,
      })
    );
  },

  async selfLearningQaQueue() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/self-learning/qa-queue"),
      () => ({
        status: "ok",
        count: 2,
        items: [
          {
            proposal_id: "prop_demo_1",
            kind: "objection",
            title: "New Objection: wait_until_ghost_month_ends",
            summary: "Khách dùng lý do tháng cô hồn để trì hoãn.",
            confidence: 0.88,
            novelty: 0.82,
            evidence_count: 14,
            quality_score: 0.79,
            status: "pending_qa",
            suggested_rule: "OBJECTION::wait_until_ghost_month_ends",
            suggested_coaching: "Thừa nhận tín ngưỡng → giữ chỗ/giá → chốt nhẹ.",
          },
          {
            proposal_id: "prop_demo_2",
            kind: "intent",
            title: "New Intent: deferred_payment_tonight",
            summary: "Khách báo sẽ chuyển khoản tối.",
            confidence: 0.91,
            novelty: 0.71,
            evidence_count: 22,
            quality_score: 0.81,
            status: "pending_qa",
            suggested_rule: "INTENT::deferred_payment_tonight",
            suggested_coaching: "Chốt giờ cụ thể + gửi STK + reminder.",
          },
        ],
      })
    );
  },

  async selfLearningIngest(call: Record<string, unknown>) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/self-learning/ingest", {
          method: "POST",
          body: { call },
        }),
      () => ({
        status: "ok",
        call_id: call.call_id || "demo-call",
        patterns: [{ text: "Để em chuyển khoản tối", kind: "buying_signal", novelty: 0.7 }],
        clusters: [{ name: "Pay Tonight Intent", size: 3 }],
        proposals: [{ proposal_id: "prop_demo_new", status: "pending_qa" }],
        pending_qa: true,
        auto_applied_to_production: false,
      })
    );
  },

  async selfLearningApprove(proposalId: string, reviewer = "qa") {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>(
          `/v1/self-learning/proposals/${proposalId}/approve`,
          { method: "POST", body: { reviewer } }
        ),
      () => ({
        status: "ok",
        proposal: { proposal_id: proposalId, status: "approved", reviewer },
        promoted_to_production: false,
      })
    );
  },

  async selfLearningReject(proposalId: string, reviewer = "qa", reason = "") {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>(
          `/v1/self-learning/proposals/${proposalId}/reject`,
          { method: "POST", body: { reviewer, reason } }
        ),
      () => ({
        status: "ok",
        proposal: { proposal_id: proposalId, status: "rejected", reviewer },
      })
    );
  },

  async selfLearningPromote(proposalId: string, reviewer = "qa") {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>(
          `/v1/self-learning/proposals/${proposalId}/promote`,
          { method: "POST", body: { reviewer } }
        ),
      () => ({
        status: "ok",
        ok: true,
        promoted: true,
        gate: { ok: true, errors: [] },
      })
    );
  },

  async selfLearningEdit(
    proposalId: string,
    patch: Record<string, unknown>,
    reviewer = "qa"
  ) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>(
          `/v1/self-learning/proposals/${proposalId}/edit`,
          { method: "POST", body: { reviewer, patch } }
        ),
      () => ({
        status: "ok",
        proposal: { proposal_id: proposalId, status: "edited", ...patch },
      })
    );
  },

  async digitalTwinDashboard() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/digital-twin/dashboard"),
      () => ({
        status: "ok",
        widgets: {
          twin_count: 1,
          avg_similarity: 0.72,
          avg_improvement: 0.61,
          roleplay_sessions: 12,
          skill_gap_index: 0.22,
          progress: 0.61,
        },
        top_differences: [{ skill: "closing", gap: 0.28 }],
        verbatim_cloning_blocked: true,
      })
    );
  },

  async digitalTwinTrain(body: {
    agent_id: string;
    display_name: string;
    calls: Record<string, unknown>[];
    activate?: boolean;
  }) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/digital-twin/train", {
          method: "POST",
          body,
        }),
      () => ({
        status: "ok",
        ok: true,
        twin: {
          twin_id: "twin_demo",
          display_name: body.display_name,
          confidence: 0.86,
          status: "active",
        },
        accepted_calls: body.calls.length,
        rejected_calls: 0,
        verbatim_cloning: false,
      })
    );
  },

  async digitalTwinRoleplay(
    twinId: string,
    body: {
      trainee_id: string;
      scenario: string;
      trainee_turns: string[];
      customer_turns?: string[];
    }
  ) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>(`/v1/digital-twin/twins/${twinId}/roleplay`, {
          method: "POST",
          body,
        }),
      () => ({
        status: "ok",
        ok: true,
        session: {
          similarity_score: 0.71,
          improvement_score: 0.64,
          coaching: ["Bám nhịp Twin: đồng cảm → hỏi nhu cầu → giá trị → chốt mềm."],
          top_differences: ["closing: gap=0.24"],
        },
      })
    );
  },

  async digitalTwinQuality() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/digital-twin/quality"),
      () => ({
        status: "ok",
        ok: true,
        checks: {
          twin_accuracy: { ok: true, value: 0.86 },
          style_consistency: { ok: true, value: 0.9 },
          coaching_quality: { ok: true, value: 0.8 },
          similarity_stability: { ok: true, value: 0.85 },
        },
        verbatim_cloning_blocked: true,
      })
    );
  },

  async selfLearningQuality() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/self-learning/quality"),
      () => ({
        status: "ok",
        ok: true,
        production_count: 9,
        pending_leaked_into_production: 0,
        quality_threshold: 0.55,
        requires_qa: true,
      })
    );
  },,

  async negotiationDashboard() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/negotiation/dashboard"),
      () => ({
        status: "ok",
        widgets: {
          win_probability: 0.62,
          next_best_action: {
            event: "next_best_action",
            kind: "value",
            script: "Nhấn mạnh giá trị theo tháng và lợi ích dài hạn.",
          },
          negotiation_timeline: [],
          strategy_evolution: [],
          session_count: 3,
          avg_exit_risk: 0.28,
          avg_buy_probability: 0.55,
        },
        strategy_picks: { value: 2, empathy: 1 },
        static_tree_forbidden: true,
      })
    );
  },

  async negotiationAnalyze(body: {
    customer_utterance: string;
    history?: Record<string, unknown>[];
    context?: Record<string, unknown>;
  }) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/negotiation/analyze", {
          method: "POST",
          body,
        }),
      () => ({
        status: "ok",
        ok: true,
        prediction: {
          next_question: "Có gói nào rẻ hơn không?",
          next_objection: "Vẫn thấy đắt.",
          next_emotion: "curious",
          exit_risk: 0.32,
          buy_probability: 0.48,
          horizon: [
            { step: 1, predicted_emotion: "curious" },
            { step: 2, predicted_emotion: "cautious" },
            { step: 3, predicted_emotion: "positive" },
          ],
          confidence: 0.72,
          evidence: ["objection_family=price"],
        },
        strategies: [
          {
            kind: "empathy",
            label: "Chiến lược đồng cảm",
            win_probability: 0.58,
            risk_score: 0.22,
            recommended_script: "Em hiểu anh/chị đang cân nhắc ngân sách.",
            forbidden_script: "Rẻ thế này không mua là tiếc.",
          },
          {
            kind: "value",
            label: "Chiến lược giá trị",
            win_probability: 0.64,
            risk_score: 0.25,
            recommended_script: "Nhìn theo tháng thì chi phí hợp lý hơn nhiều.",
            forbidden_script: "Đắt thì đắt, không giảm đâu.",
          },
        ],
        best_strategy: {
          kind: "value",
          win_probability: 0.64,
          recommended_script: "Nhìn theo tháng thì chi phí hợp lý hơn nhiều.",
        },
        next_best_action: "Nhìn theo tháng thì chi phí hợp lý hơn nhiều.",
        static_tree: false,
      })
    );
  },

  async negotiationCompare(body: {
    customer_utterance: string;
    context?: Record<string, unknown>;
  }) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/negotiation/compare", {
          method: "POST",
          body,
        }),
      () => ({
        status: "ok",
        ok: true,
        comparisons: [
          { rank: 1, kind: "value", win_probability: 0.64, risk_score: 0.25, utility: 0.53 },
          { rank: 2, kind: "empathy", win_probability: 0.58, risk_score: 0.22, utility: 0.48 },
        ],
        best: { kind: "value", win_probability: 0.64 },
      })
    );
  },

  async negotiationQuality() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/negotiation/quality"),
      () => ({
        status: "ok",
        ok: true,
        checks: {
          prediction_accuracy: { ok: true, value: 0.86 },
          strategy_consistency: { ok: true, value: 0.9 },
          evidence_validation: { ok: true, value: 0.88 },
          confidence_stability: { ok: true, value: 0.8 },
        },
        static_tree_forbidden: true,
        strategy_graph_enabled: true,
      })
    );
  },

  async cltvDashboard() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/cltv/dashboard"),
      () => ({
        status: "ok",
        widgets: {
          cltv_forecast: 0.71,
          churn_forecast: 0.28,
          avg_lifetime_value: 2_450_000,
          upsell_opportunity: [{ lead_id: "L1", upsell_score: 0.72 }],
          referral_opportunity: [{ lead_id: "L2", referral_score: 0.61 }],
          prediction_count: 12,
          priority_counts: { high_value: 4, medium: 5, low: 3 },
        },
      })
    );
  },

  async cltvPredict(body: {
    lead_id?: string;
    call_history?: Record<string, unknown>[];
    conversation_dna?: Record<string, unknown>;
    intent?: string | Record<string, unknown>;
    emotion?: string | Record<string, unknown>;
    buying_signal?: number | Record<string, unknown>;
    crm?: Record<string, unknown>;
    follow_up?: Record<string, unknown>;
  }) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/cltv/predict", {
          method: "POST",
          body,
        }),
      () => ({
        status: "ok",
        ok: true,
        priority: "high_value",
        lifetime_value: 3_200_000,
        churn_risk: 0.22,
        scores: {
          cltv_score: 0.78,
          retention_score: 0.74,
          upsell_score: 0.7,
          cross_sell_score: 0.62,
          referral_score: 0.58,
          lifetime_value: 3_200_000,
          priority: "high_value",
          confidence: 0.8,
        },
      })
    );
  },

  async cltvPrioritize(body: { leads: Record<string, unknown>[] }) {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/cltv/prioritize", {
          method: "POST",
          body,
        }),
      () => ({
        status: "ok",
        ok: true,
        ranked: (body.leads || []).map((lead, idx) => ({
          lead_id: lead.lead_id || `lead-${idx}`,
          scores: {
            priority: idx === 0 ? "high_value" : idx === 1 ? "low" : "medium",
            cltv_score: Math.max(0.2, 0.85 - idx * 0.25),
            lifetime_value: Math.max(200_000, 3_000_000 - idx * 1_000_000),
          },
        })),
        counts: { high_value: 1, medium: 1, low: 1 },
      })
    );
  },

  async cltvQuality() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/cltv/quality"),
      () => ({
        status: "ok",
        ok: true,
        checks: {
          forecast_accuracy: { ok: true, value: 0.88 },
          stability: { ok: true, value: 0.8 },
          explainability: { ok: true, value: 0.9 },
          evidence_validation: { ok: true, value: 0.9 },
        },
        separation_ok: true,
      })
    );
  },

  async warRoomDashboard() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/war-room/dashboard"),
      () => ({
        status: "ok",
        widgets: {
          active_calls: 12,
          online_agents: 18,
          queue_size: 9,
          avg_wait_sec: 22,
          conversion_rate: 0.21,
          open_alerts: 3,
          critical_alerts: 1,
          avg_buy_signal: 0.48,
        },
        recent_alerts: [
          { alert_id: "a1", kind: "conversion_drop", severity: "high", title: "Conversion drop", message: "Floor conversion below baseline" },
        ],
        realtime_enabled: true,
      })
    );
  },

  async warRoomScanAlerts() {
    return withDemoFallback(
      () =>
        request<Record<string, unknown>>("/v1/war-room/alerts/scan", {
          method: "POST",
          body: {},
        }),
      () => ({
        status: "ok",
        ok: true,
        count: 2,
        alerts: [
          { alert_id: "a1", kind: "sla_breach", severity: "critical", title: "SLA breach", message: "Wait > 60s" },
          { alert_id: "a2", kind: "objection_spike", severity: "medium", title: "Objection spike", message: "Price objections rising" },
        ],
      })
    );
  },

  async warRoomQuality() {
    return withDemoFallback(
      () => request<Record<string, unknown>>("/v1/war-room/quality"),
      () => ({
        status: "ok",
        ok: true,
        realtime_enabled: true,
        checks: {
          alert_precision: { ok: true, value: 0.9 },
          freshness: { ok: true, value: 0.88 },
          coverage: { ok: true, value: 0.8 },
          evidence_validation: { ok: true, value: 0.92 },
        },
      })
    );
  },


};
