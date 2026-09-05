"use client";

import type { ConversationDNA } from "@/lib/types";

type Props = {
  dna: ConversationDNA;
  size?: number;
};

export function RadarDNA({ dna, size = 320 }: Props) {
  const dims = dna.dimensions;
  const n = dims.length;
  const cx = size / 2;
  const cy = size / 2;
  const maxR = size * 0.38;

  const point = (i: number, value: number) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    const r = (Math.max(0, Math.min(100, value)) / 100) * maxR;
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      lx: cx + (maxR + 18) * Math.cos(angle),
      ly: cy + (maxR + 18) * Math.sin(angle),
      angle,
    };
  };

  const rings = [0.25, 0.5, 0.75, 1];
  const polygon = dims
    .map((d, i) => {
      const p = point(i, d.score);
      return `${p.x},${p.y}`;
    })
    .join(" ");

  return (
    <div className="space-y-3">
      <div>
        <div className="text-sm font-medium text-slate-200">Conversation DNA</div>
        <p className="mt-1 text-sm text-slate-400">{dna.summary}</p>
      </div>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="mx-auto max-w-full"
        role="img"
        aria-label="Radar Conversation DNA"
      >
        {rings.map((r) => (
          <polygon
            key={r}
            fill="none"
            stroke="rgba(148,163,184,0.18)"
            strokeWidth="1"
            points={dims
              .map((_, i) => {
                const p = point(i, r * 100);
                return `${p.x},${p.y}`;
              })
              .join(" ")}
          />
        ))}
        {dims.map((_, i) => {
          const p = point(i, 100);
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={p.x}
              y2={p.y}
              stroke="rgba(148,163,184,0.2)"
            />
          );
        })}
        <polygon
          points={polygon}
          fill="rgba(45,212,191,0.22)"
          stroke="#2dd4bf"
          strokeWidth="2"
        />
        {dims.map((d, i) => {
          const p = point(i, d.score);
          const lab = point(i, 100);
          return (
            <g key={d.key}>
              <circle cx={p.x} cy={p.y} r={3.5} fill="#5eead4" />
              <text
                x={lab.lx}
                y={lab.ly}
                textAnchor="middle"
                dominantBaseline="middle"
                className="fill-slate-300"
                fontSize="10"
              >
                {d.label}
              </text>
              <text
                x={lab.lx}
                y={lab.ly + 12}
                textAnchor="middle"
                className="fill-slate-500"
                fontSize="9"
              >
                {Math.round(d.score)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
