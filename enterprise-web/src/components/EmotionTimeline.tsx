"use client";

import type { EmotionPoint } from "@/lib/types";

type Props = {
  points: EmotionPoint[];
  height?: number;
};

export function EmotionTimeline({ points, height = 120 }: Props) {
  if (!points.length) {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-6 text-sm text-amber-200">
        Emotion timeline không khả dụng — Insufficient Evidence (thiếu prosody).
      </div>
    );
  }

  const w = 640;
  const pad = 16;
  const mid = height / 2;

  const coords = points.map((p) => {
    const x = pad + (p.progress_pct / 100) * (w - pad * 2);
    const y = mid - p.valence * (height / 2 - pad);
    return { x, y, p };
  });

  const line = coords
    .map((c, i) => `${i === 0 ? "M" : "L"} ${c.x.toFixed(1)} ${c.y.toFixed(1)}`)
    .join(" ");

  const heatCells = Array.from({ length: 20 }, (_, i) => {
    const from = i * 5;
    const to = from + 5;
    const bucket = points.filter(
      (p) => p.progress_pct >= from && p.progress_pct < to
    );
    const avg =
      bucket.length === 0
        ? 0
        : bucket.reduce((s, p) => s + p.valence, 0) / bucket.length;
    return { from, to, avg };
  });

  return (
    <div className="space-y-3">
      <div className="text-sm font-medium text-slate-200">
        Emotion timeline / heatmap
      </div>
      <div
        className="grid gap-0.5"
        style={{ gridTemplateColumns: "repeat(20, minmax(0, 1fr))" }}
      >
        {heatCells.map((c) => {
          const pos = (c.avg + 1) / 2;
          const color =
            c.avg < -0.15
              ? `rgba(251, 113, 133, ${0.25 + pos * 0.5})`
              : c.avg > 0.15
                ? `rgba(52, 211, 153, ${0.25 + pos * 0.5})`
                : `rgba(148, 163, 184, ${0.2 + Math.abs(c.avg) * 0.4})`;
          return (
            <div
              key={c.from}
              className="h-3 rounded-sm"
              style={{ background: color }}
              title={`${c.from}–${c.to}% · valence ${c.avg.toFixed(2)}`}
            />
          );
        })}
      </div>
      <svg
        viewBox={`0 0 ${w} ${height}`}
        className="h-auto w-full rounded-lg border border-slate-800 bg-slate-950/60"
        role="img"
        aria-label="Biểu đồ valence theo tiến trình cuộc gọi"
      >
        <line
          x1={pad}
          x2={w - pad}
          y1={mid}
          y2={mid}
          stroke="rgba(148,163,184,0.25)"
          strokeDasharray="4 4"
        />
        <path d={line} fill="none" stroke="#2dd4bf" strokeWidth="2.2" />
        {coords.map((c) => (
          <g key={`${c.p.t_sec}-${c.p.speaker}`}>
            <circle
              cx={c.x}
              cy={c.y}
              r={4.5}
              fill={c.p.speaker === "customer" ? "#fb7185" : "#38bdf8"}
            >
              <title>
                {c.p.label} · {c.p.speaker} · t={c.p.t_sec}s · valence=
                {c.p.valence}
              </title>
            </circle>
          </g>
        ))}
        <text x={pad} y={14} className="fill-slate-500" fontSize="10">
          +valence
        </text>
        <text x={pad} y={height - 6} className="fill-slate-500" fontSize="10">
          −valence
        </text>
      </svg>
      <div className="flex gap-4 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-sky-400" /> Sale
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-rose-400" /> Khách hàng
        </span>
      </div>
    </div>
  );
}
