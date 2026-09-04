/**
 * Server-side client for ChốtKiểm (222.255.215.55).
 * Credentials come from env — never hardcode secrets in the client bundle.
 */

export type ChotKiemLiveStats = {
  totals: {
    calls: number;
    avgScore: number;
    completeCalls: number;
    uniquePhones: number;
    agents: number;
  };
  criteria: {
    passRate: number;
    avgScore: number;
    coreFailFrequency: Array<{ key: string; label: string; failCount: number }>;
  };
  fetchedAt: string;
  baseUrl: string;
};

export type ChotKiemRemoteCall = {
  id: string;
  employeeName?: string;
  phoneNumber?: string;
  createdAt?: number;
  overallScore?: number;
  grade?: string;
  isComplete?: boolean;
  callSummary?: string;
  transcript?: string;
  scorecard?: {
    criteria?: Array<{
      key: string;
      label?: string;
      passed: boolean;
      required?: boolean;
      value?: string;
      evidence?: string | null;
    }>;
  };
};

function config() {
  const baseUrl = (process.env.CHOTKIEM_BASE_URL || "http://222.255.215.55").replace(/\/$/, "");
  const user = process.env.CHOTKIEM_USER || "";
  const password = process.env.CHOTKIEM_PASSWORD || "";
  if (!user || !password) {
    throw new Error("Thiếu CHOTKIEM_USER / CHOTKIEM_PASSWORD trong .env.local");
  }
  return { baseUrl, user, password };
}

function authHeader(user: string, password: string) {
  return "Basic " + Buffer.from(`${user}:${password}`).toString("base64");
}

async function ckFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const { baseUrl, user, password } = config();
  const res = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      Authorization: authHeader(user, password),
      "X-Hub-User": user,
      "X-Hub-Pass": password,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`ChốtKiểm ${path} → ${res.status}: ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export async function fetchChotKiemStats(): Promise<ChotKiemLiveStats> {
  const [dashboard, criteria] = await Promise.all([
    ckFetch<{
      totals: ChotKiemLiveStats["totals"];
    }>("/api/dashboard"),
    ckFetch<{
      passRate: number;
      avgScore: number;
      coreFailFrequency: Array<{ key: string; label: string; failCount: number }>;
    }>("/api/calls/criteria-report?limit=100"),
  ]);

  return {
    totals: dashboard.totals,
    criteria: {
      passRate: criteria.passRate,
      avgScore: criteria.avgScore,
      coreFailFrequency: [...(criteria.coreFailFrequency || [])]
        .sort((a, b) => b.failCount - a.failCount)
        .slice(0, 8),
    },
    fetchedAt: new Date().toISOString(),
    baseUrl: config().baseUrl,
  };
}

export async function fetchChotKiemSampleCalls(limit = 8): Promise<ChotKiemRemoteCall[]> {
  const report = await ckFetch<{
    rows: Array<{ id: string; grade?: string }>;
  }>("/api/calls/criteria-report?limit=40");

  const rows = report.rows || [];
  const pick: string[] = [];
  for (const grade of ["A", "F", "B", "C", "D"]) {
    for (const r of rows) {
      if (r.grade === grade && !pick.includes(r.id)) pick.push(r.id);
      if (pick.length >= limit) break;
    }
    if (pick.length >= limit) break;
  }
  for (const r of rows) {
    if (pick.length >= limit) break;
    if (!pick.includes(r.id)) pick.push(r.id);
  }

  const calls: ChotKiemRemoteCall[] = [];
  for (const id of pick.slice(0, limit)) {
    try {
      const call = await ckFetch<ChotKiemRemoteCall>(`/api/calls/${id}`);
      if (call?.transcript) calls.push(call);
    } catch {
      // skip failed individual fetches
    }
  }
  return calls;
}

export function maskPhone(phone?: string): string {
  if (!phone) return "***";
  const digits = phone.replace(/\D/g, "");
  if (digits.length < 6) return "***";
  return `${digits.slice(0, 3)}***${digits.slice(-3)}`;
}
