"use client";

import { formatNumber, formatVnd } from "@/lib/format";
import type { RevenueLeak } from "@/lib/types";

type RollupComponent = {
  cause_code: string;
  amount: number;
  call_count: number;
};

type Props = {
  leak?: RevenueLeak;
  rollup?: {
    estimated_total: number;
    currency: string;
    insufficient_evidence_calls: number;
    top_components: RollupComponent[];
  };
};

export function RevenueLeakChart({ leak, rollup }: Props) {
  if (leak && leak.status === "Insufficient Evidence") {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-4 text-sm text-amber-100">
        <div className="font-medium">Revenue leak — Insufficient Evidence</div>
        <p className="mt-1 text-amber-200/90">{leak.explanation}</p>
      </div>
    );
  }

  const bars =
    rollup?.top_components ??
    (leak
      ? leak.leak_codes.map((code, i) => ({
          cause_code: code,
          amount: (leak.estimated_loss_vnd ?? 0) / Math.max(1, leak.leak_codes.length - i * 0.15),
          call_count: 1,
        }))
      : []);

  const max = Math.max(...bars.map((b) => b.amount), 1);
  const total =
    rollup?.estimated_total ?? leak?.estimated_loss_vnd ?? bars.reduce((s, b) => s + b.amount, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-200">Rò rỉ doanh thu</div>
          <div className="mt-1 text-2xl font-semibold tracking-tight text-rose-300">
            {formatVnd(total)}
          </div>
        </div>
        <div className="text-right text-xs text-slate-500">
          {leak?.probability != null ? (
            <div>Xác suất mất deal {(leak.probability * 100).toFixed(0)}%</div>
          ) : null}
          {rollup ? (
            <div>IE (không cộng tiền): {rollup.insufficient_evidence_calls} cuộc</div>
          ) : null}
        </div>
      </div>

      {leak?.explanation ? (
        <p className="text-sm leading-relaxed text-slate-400">{leak.explanation}</p>
      ) : null}

      <div className="space-y-2.5">
        {bars.map((b) => (
          <div key={b.cause_code}>
            <div className="mb-1 flex justify-between gap-2 text-xs">
              <span className="font-mono text-slate-300">{b.cause_code}</span>
              <span className="text-slate-400">
                {formatVnd(b.amount)} · {formatNumber(b.call_count)} cuộc
              </span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-gradient-to-r from-rose-700 to-amber-500 transition-all duration-500"
                style={{ width: `${(b.amount / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
