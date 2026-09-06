"use client";

import type { Coaching } from "@/lib/types";

type Props = {
  coaching: Coaching;
};

const PRIORITY: Record<string, string> = {
  high: "Cao",
  medium: "Trung bình",
  low: "Thấp",
};

export function CoachingPanel({ coaching }: Props) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium text-slate-200">Gợi ý coaching</div>
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            coaching.priority === "high"
              ? "bg-rose-500/15 text-rose-300"
              : coaching.priority === "medium"
                ? "bg-amber-500/15 text-amber-300"
                : "bg-slate-700 text-slate-300"
          }`}
        >
          Ưu tiên: {PRIORITY[coaching.priority]}
        </span>
      </div>

      {coaching.tips.length === 0 ? (
        <p className="text-sm text-slate-500">Không có tip coaching cho cuộc gọi này.</p>
      ) : (
        <ul className="space-y-3">
          {coaching.tips.map((tip) => (
            <li
              key={tip.tip_id}
              className="rounded-lg border border-slate-800 bg-slate-900/50 p-3"
            >
              <div className="text-sm font-medium text-slate-100">{tip.title}</div>
              <blockquote className="mt-2 border-l-2 border-teal-500/60 pl-3 text-sm italic leading-relaxed text-slate-300">
                “{tip.script_suggestion}”
              </blockquote>
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs text-slate-500">
                {tip.linked_rule_ids.map((r) => (
                  <span key={r} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono">
                    {r}
                  </span>
                ))}
                {tip.evidence_refs.map((e) => (
                  <span key={e} className="rounded bg-slate-800 px-1.5 py-0.5">
                    {e}
                  </span>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}

      {coaching.drill_ids.length > 0 ? (
        <div>
          <div className="mb-1.5 text-xs uppercase tracking-wide text-slate-500">
            Roleplay / drill
          </div>
          <div className="flex flex-wrap gap-2">
            {coaching.drill_ids.map((d) => (
              <span
                key={d}
                className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1 font-mono text-xs text-teal-300"
              >
                {d}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
