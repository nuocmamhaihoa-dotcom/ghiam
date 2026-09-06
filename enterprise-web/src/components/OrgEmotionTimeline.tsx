"use client";

type EmotionPoint = {
  progress_pct: number;
  avg_valence: number;
  samples: number;
  label: string;
};

export function OrgEmotionTimeline({
  points,
  title = "Timeline cảm xúc tổ chức",
}: {
  points: EmotionPoint[];
  title?: string;
}) {
  if (!points.length) {
    return (
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-100">{title}</h3>
        <p className="text-sm text-slate-500">Insufficient Evidence: chưa đủ DNA cảm xúc.</p>
      </div>
    );
  }

  const width = 320;
  const height = 120;
  const pad = 12;
  const xs = points.map((p) => p.progress_pct);
  const ys = points.map((p) => p.avg_valence);
  const minX = Math.min(...xs, 0);
  const maxX = Math.max(...xs, 100);
  const minY = Math.min(-1, ...ys);
  const maxY = Math.max(1, ...ys);
  const scaleX = (v: number) =>
    pad + ((v - minX) / Math.max(maxX - minX, 1)) * (width - pad * 2);
  const scaleY = (v: number) =>
    height - pad - ((v - minY) / Math.max(maxY - minY, 1)) * (height - pad * 2);
  const d = points
    .map(
      (p, i) =>
        `${i === 0 ? "M" : "L"} ${scaleX(p.progress_pct)} ${scaleY(p.avg_valence)}`
    )
    .join(" ");

  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-slate-100">{title}</h3>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full overflow-visible">
        <line
          x1={pad}
          x2={width - pad}
          y1={scaleY(0)}
          y2={scaleY(0)}
          stroke="rgba(148,163,184,0.35)"
          strokeDasharray="4 4"
        />
        <path d={d} fill="none" stroke="rgb(45,212,191)" strokeWidth="2.5" />
        {points.map((p) => (
          <circle
            key={p.progress_pct}
            cx={scaleX(p.progress_pct)}
            cy={scaleY(p.avg_valence)}
            r={3.5}
            fill={
              p.label === "positive"
                ? "rgb(52,211,153)"
                : p.label === "negative"
                  ? "rgb(251,113,133)"
                  : "rgb(148,163,184)"
            }
          >
            <title>
              {p.progress_pct}% · valence {p.avg_valence} · n={p.samples}
            </title>
          </circle>
        ))}
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>Đầu cuộc gọi</span>
        <span>Cuối cuộc gọi</span>
      </div>
    </div>
  );
}
