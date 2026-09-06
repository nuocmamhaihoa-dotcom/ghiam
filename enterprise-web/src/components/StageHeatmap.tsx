"use client";

import { STAGE_LABELS, scoreFill } from "@/lib/format";
import type { StageKey } from "@/lib/types";

type Row = {
  agent_name: string;
  stages: Partial<Record<StageKey, number | null>>;
};

const STAGES: StageKey[] = [
  "opening",
  "discovery",
  "pitch",
  "objection",
  "close",
  "outro",
];

type Props = {
  rows: Row[];
  title?: string;
};

export function StageHeatmap({ rows, title = "Heatmap giai đoạn × nhân viên" }: Props) {
  return (
    <div className="w-full overflow-x-auto">
      <div className="mb-3 text-sm font-medium text-slate-200">{title}</div>
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr>
            <th className="px-2 py-2 text-left font-medium text-slate-400">
              Nhân viên
            </th>
            {STAGES.map((s) => (
              <th
                key={s}
                className="px-2 py-2 text-center font-medium text-slate-400"
              >
                {STAGE_LABELS[s]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.agent_name} className="border-t border-slate-800/80">
              <td className="whitespace-nowrap px-2 py-2 text-slate-200">
                {row.agent_name}
              </td>
              {STAGES.map((s) => {
                const v = row.stages[s];
                const bg =
                  v == null
                    ? "repeating-linear-gradient(135deg, rgba(245,158,11,0.25) 0 4px, rgba(245,158,11,0.08) 4px 8px)"
                    : `color-mix(in srgb, ${scoreFill(v)} ${Math.max(18, v)}%, #0f172a)`;
                return (
                  <td key={s} className="px-1 py-1">
                    <div
                      className="flex h-9 items-center justify-center rounded-md border border-slate-700/40 text-xs font-semibold text-slate-100"
                      style={{ background: bg }}
                      title={
                        v == null
                          ? "Insufficient Evidence"
                          : `${STAGE_LABELS[s]}: ${v}`
                      }
                    >
                      {v == null ? "IE" : Math.round(v)}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
