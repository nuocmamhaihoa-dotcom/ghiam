"use client";

type ParetoItem = {
  cause_code: string;
  label: string;
  count: number;
  share: number;
  cumulative_share: number;
};

export function ParetoChart({
  items,
  title = "Pareto nguyên nhân gốc",
}: {
  items: ParetoItem[];
  title?: string;
}) {
  const max = Math.max(...items.map((i) => i.count), 1);
  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-slate-100">{title}</h3>
      <div className="space-y-3">
        {items.map((item) => (
          <div key={item.cause_code}>
            <div className="mb-1 flex items-center justify-between gap-2 text-xs">
              <div className="min-w-0">
                <div className="font-mono text-[10px] text-teal-300">{item.cause_code}</div>
                <div className="truncate text-slate-200">{item.label}</div>
              </div>
              <div className="shrink-0 text-right text-slate-400">
                {item.count} · {(item.cumulative_share * 100).toFixed(0)}% lũy kế
              </div>
            </div>
            <div className="relative h-2 rounded-full bg-slate-800">
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-rose-400/80"
                style={{ width: `${Math.round((item.count / max) * 100)}%` }}
              />
              <div
                className="absolute top-1/2 h-2 w-2 -translate-y-1/2 rounded-full border border-amber-200 bg-amber-400"
                style={{
                  left: `calc(${Math.round(item.cumulative_share * 100)}% - 4px)`,
                }}
                title={`Lũy kế ${(item.cumulative_share * 100).toFixed(1)}%`}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
