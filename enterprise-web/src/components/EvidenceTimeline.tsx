"use client";

import { formatTs } from "@/lib/format";
import type { EvidenceSpan } from "@/lib/types";

type Props = {
  evidence: EvidenceSpan[];
  onSelect?: (ev: EvidenceSpan) => void;
  selectedId?: string;
};

const SPEAKER_LABEL = {
  agent: "Sale",
  customer: "KH",
  unknown: "?",
} as const;

export function EvidenceTimeline({ evidence, onSelect, selectedId }: Props) {
  const sorted = [...evidence].sort(
    (a, b) => a.audio_ts_start - b.audio_ts_start
  );

  if (!sorted.length) {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-6 text-sm text-amber-200">
        Không có evidence span — Insufficient Evidence.
      </div>
    );
  }

  return (
    <ol className="relative space-y-0 border-l border-slate-700 pl-5">
      {sorted.map((ev, idx) => {
        const active = selectedId === ev.evidence_id;
        return (
          <li key={ev.evidence_id} className="relative pb-5 last:pb-0">
            <span
              className={`absolute -left-[1.55rem] top-1.5 h-2.5 w-2.5 rounded-full border-2 ${
                active
                  ? "border-teal-300 bg-teal-400"
                  : "border-slate-600 bg-slate-800"
              }`}
            />
            <button
              type="button"
              onClick={() => onSelect?.(ev)}
              className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                active
                  ? "border-teal-500/50 bg-teal-500/10"
                  : "border-slate-800 bg-slate-900/40 hover:border-slate-600"
              }`}
            >
              <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                <span className="font-mono text-teal-300/90">
                  {formatTs(ev.audio_ts_start)}–{formatTs(ev.audio_ts_end)}
                </span>
                <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">
                  {SPEAKER_LABEL[ev.speaker]}
                </span>
                <span className="rounded bg-slate-800 px-1.5 py-0.5">
                  {ev.type}
                </span>
                {ev.stage ? (
                  <span className="rounded bg-slate-800 px-1.5 py-0.5">
                    {ev.stage}
                  </span>
                ) : null}
                <span>conf {(ev.confidence * 100).toFixed(0)}%</span>
                <span className="text-slate-600">#{idx + 1}</span>
              </div>
              <p className="text-sm leading-relaxed text-slate-200">
                “{ev.quote}”
              </p>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
