"use client";

import { formatNumber, scoreFill } from "@/lib/format";

type Props = {
  score: number | null;
  label?: string;
  size?: number;
  sublabel?: string;
};

export function ScoreGauge({
  score,
  label = "Điểm tổng",
  size = 160,
  sublabel,
}: Props) {
  const stroke = 12;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score)) / 100;
  const offset = c * (1 - pct);
  const color = scoreFill(score);

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width={size} height={size} className="drop-shadow-sm" aria-label={label}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="rgba(148,163,184,0.15)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          className="transition-[stroke-dashoffset] duration-700 ease-out"
        />
        <text
          x="50%"
          y="46%"
          textAnchor="middle"
          dominantBaseline="middle"
          className="fill-slate-100"
          style={{ fontSize: size * 0.22, fontWeight: 650 }}
        >
          {score == null ? "IE" : formatNumber(score, 1)}
        </text>
        <text
          x="50%"
          y="62%"
          textAnchor="middle"
          className="fill-slate-400"
          style={{ fontSize: size * 0.08 }}
        >
          {score == null ? "Thiếu BC" : "/ 100"}
        </text>
      </svg>
      <div className="text-center">
        <div className="text-sm font-medium text-slate-200">{label}</div>
        {sublabel ? (
          <div className="text-xs text-slate-500">{sublabel}</div>
        ) : null}
      </div>
    </div>
  );
}
