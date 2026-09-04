import {
  analyzeCallTranscript,
  buildTeamInsights,
  type CallAnalysis,
  type CallOutcome,
} from "./analyzeCall";
import { CHOTKIEM_SEED_CALLS } from "./chotKiemSeed";
import { DEMO_CALLS } from "./demoData";

export type StoredCall = {
  id: string;
  title: string;
  industry: string;
  product: string;
  agentName: string;
  outcome: CallOutcome;
  durationSec: number;
  transcript: string;
  createdAt: number;
  analysis: CallAnalysis;
  source?: "demo" | "chotkiem" | "manual";
  externalId?: string;
  phoneMasked?: string;
  liveGrade?: string;
  liveScore?: number;
  liveSummary?: string;
};

const KEY = "telesale-coach-calls-v3";

function uid() {
  return `call_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function seed(): StoredCall[] {
  const demos: StoredCall[] = DEMO_CALLS.map((c, i) => ({
    id: `demo_${i + 1}`,
    title: c.title,
    industry: c.industry,
    product: c.product,
    agentName: c.agentName,
    outcome: c.outcome,
    durationSec: c.durationSec,
    transcript: c.transcript,
    createdAt: Date.now() - (DEMO_CALLS.length - i) * 3600_000,
    analysis: analyzeCallTranscript({
      transcript: c.transcript,
      durationSec: c.durationSec,
      outcome: c.outcome,
    }),
    source: "demo",
  }));

  const live: StoredCall[] = CHOTKIEM_SEED_CALLS.map((c, i) => ({
    id: c.id,
    title: c.title,
    industry: c.industry,
    product: c.product,
    agentName: c.agentName,
    outcome: c.outcome,
    durationSec: c.durationSec,
    transcript: c.transcript,
    createdAt: Date.now() - (i + 1) * 1800_000,
    analysis: analyzeCallTranscript({
      transcript: c.transcript,
      durationSec: c.durationSec,
      outcome: c.outcome,
    }),
    source: "chotkiem",
    externalId: c.externalId,
    phoneMasked: c.phoneMasked,
    liveGrade: c.liveGrade,
    liveScore: c.liveScore,
    liveSummary: c.liveSummary,
  }));

  return [...live, ...demos];
}

export function loadCalls(): StoredCall[] {
  if (typeof window === "undefined") return seed();
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) {
      const data = seed();
      localStorage.setItem(KEY, JSON.stringify(data));
      return data;
    }
    return JSON.parse(raw) as StoredCall[];
  } catch {
    return seed();
  }
}

export function saveCalls(calls: StoredCall[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(KEY, JSON.stringify(calls));
}

export function addCall(input: {
  title: string;
  industry: string;
  product: string;
  agentName: string;
  outcome: CallOutcome;
  durationSec: number;
  transcript: string;
  source?: StoredCall["source"];
  externalId?: string;
  phoneMasked?: string;
  liveGrade?: string;
  liveScore?: number;
  liveSummary?: string;
}): StoredCall {
  const call: StoredCall = {
    id: uid(),
    title: input.title,
    industry: input.industry,
    product: input.product,
    agentName: input.agentName,
    outcome: input.outcome,
    durationSec: input.durationSec,
    transcript: input.transcript,
    createdAt: Date.now(),
    analysis: analyzeCallTranscript({
      transcript: input.transcript,
      durationSec: input.durationSec,
      outcome: input.outcome,
    }),
    source: input.source ?? "manual",
    externalId: input.externalId,
    phoneMasked: input.phoneMasked,
    liveGrade: input.liveGrade,
    liveScore: input.liveScore,
    liveSummary: input.liveSummary,
  };
  const next = [call, ...loadCalls()];
  saveCalls(next);
  return call;
}

export function deleteCall(id: string) {
  saveCalls(loadCalls().filter((c) => c.id !== id));
}

export function resetDemoCalls() {
  const data = seed();
  saveCalls(data);
  return data;
}

/** Merge freshly synced ChốtKiểm samples (dedupe by externalId). */
export function mergeChotKiemSamples(
  samples: Array<{
    externalId: string;
    title: string;
    industry: string;
    product: string;
    agentName: string;
    outcome: CallOutcome;
    durationSec: number;
    transcript: string;
    phoneMasked?: string;
    liveGrade?: string;
    liveScore?: number;
    liveSummary?: string;
  }>,
): StoredCall[] {
  const existing = loadCalls();
  const seen = new Set(
    existing.map((c) => c.externalId).filter((x): x is string => Boolean(x)),
  );
  const incoming: StoredCall[] = [];
  for (const s of samples) {
    if (!s.transcript?.trim() || seen.has(s.externalId)) continue;
    seen.add(s.externalId);
    incoming.push({
      id: `ck_${s.externalId.slice(0, 8)}`,
      title: s.title,
      industry: s.industry,
      product: s.product,
      agentName: s.agentName,
      outcome: s.outcome,
      durationSec: s.durationSec,
      transcript: s.transcript,
      createdAt: Date.now(),
      analysis: analyzeCallTranscript({
        transcript: s.transcript,
        durationSec: s.durationSec,
        outcome: s.outcome,
      }),
      source: "chotkiem",
      externalId: s.externalId,
      phoneMasked: s.phoneMasked,
      liveGrade: s.liveGrade,
      liveScore: s.liveScore,
      liveSummary: s.liveSummary,
    });
  }
  const next = [...incoming, ...existing];
  saveCalls(next);
  return next;
}

export function getInsights(calls: StoredCall[]) {
  return buildTeamInsights(
    calls.map((c) => ({
      industry: c.industry,
      outcome: c.outcome,
      analysis: c.analysis,
    })),
  );
}
