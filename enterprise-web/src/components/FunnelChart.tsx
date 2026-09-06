"use client";

type FunnelItem = {
  stage: string;
  label: string;
  count: number;
  rate: number;
};

export function FunnelChart({
  items,
  title = "Funnel chuyển đổi theo giai đoạn",
}: {
  items: FunnelItem[];
  title?: string;
}) {
  const max = Math.max(...items.map((i) => i.count), 1);
  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-slate-100">{title}</h3>
      <div className="space-y-2">
        {items.map((item) => {
          const width = Math.max(12, Math.round((item.count / max) * 100));
          return (
            <div key={item.stage} className="grid grid-cols-[7rem_1fr_4.5rem] items-center gap-2">
              <div className="truncate text-xs text-slate-400">{item.label}</div>
              <div className="h-7 rounded-md bg-slate-900/80">
                <div
                  className="flex h-full items-center rounded-md bg-gradient-to-r from-teal-700/80 to-cyan-500/70 px-2 text-[11px] font-medium text-teal-50 transition-all duration-700"
                  style={{ width: `${width}%` }}
                >
                  {item.count.toLocaleString("vi-VN")}
                </div>
              </div>
              <div className="text-right text-xs text-slate-300">
                {(item.rate * 100).toFixed(1)}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
