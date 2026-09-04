import {
  analyzeCallTranscript,
  buildTeamInsights,
  type CallAnalysis,
  type CallOutcome,
} from "./analyzeCall";
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
};

const KEY = "telesale-coach-calls-v2";

function uid() {
  return `call_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function seed(): StoredCall[] {
  return DEMO_CALLS.map((c, i) => ({
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
  }));
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
}): StoredCall {
  const call: StoredCall = {
    id: uid(),
    ...input,
    createdAt: Date.now(),
    analysis: analyzeCallTranscript({
      transcript: input.transcript,
      durationSec: input.durationSec,
      outcome: input.outcome,
    }),
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

export function getInsights(calls: StoredCall[]) {
  return buildTeamInsights(
    calls.map((c) => ({
      industry: c.industry,
      outcome: c.outcome,
      analysis: c.analysis,
    })),
  );
}
