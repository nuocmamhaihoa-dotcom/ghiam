import clsx from "clsx";
import { Activity, Layers, MonitorSmartphone, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { formatNumber } from "../../lib/format";
import type { ProxyFilters, ProxyStats } from "../../lib/types";

type ChipTone = "slate" | "green" | "red" | "amber" | "violet" | "blue";

const CHIP_DOTS: Record<ChipTone, string> = {
  slate: "bg-slate-400",
  green: "bg-emerald-500",
  red: "bg-rose-500",
  amber: "bg-amber-500",
  violet: "bg-violet-500",
  blue: "bg-sky-500",
};

interface ChipProps {
  label: string;
  value: number | undefined;
  tone: ChipTone;
  active: boolean;
  onClick: () => void;
}

function Chip({ label, value, tone, active, onClick }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={active ? "Bấm để bỏ lọc" : "Bấm để lọc danh sách"}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 transition-colors ring-inset",
        active
          ? "bg-indigo-600 text-white ring-indigo-600"
          : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50 hover:text-slate-900",
      )}
    >
      <span className={clsx("size-1.5 rounded-full", active ? "bg-white" : CHIP_DOTS[tone])} />
      {label}
      <span className={clsx("tabular-nums", active ? "text-indigo-100" : "text-slate-400")}>
        {value === undefined ? "…" : formatNumber(value)}
      </span>
    </button>
  );
}

interface CardProps {
  icon: ReactNode;
  title: string;
  value: number | undefined;
  unit: string;
  note: string;
  children: ReactNode;
}

function Card({ icon, title, value, unit, note, children }: CardProps) {
  return (
    <div className="flex flex-col rounded-xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
      <div className="flex items-center gap-2 text-sm font-medium text-slate-500">
        <span className="flex size-7 items-center justify-center rounded-lg bg-slate-100 text-slate-500">{icon}</span>
        {title}
      </div>
      <p className="mt-3 flex items-baseline gap-1.5">
        <span className="text-2xl font-semibold text-slate-900 tabular-nums">
          {value === undefined ? "…" : formatNumber(value)}
        </span>
        <span className="text-sm text-slate-500">{unit}</span>
      </p>
      <p className="mt-0.5 text-xs text-slate-500">{note}</p>
      <div className="mt-3 flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

interface StatsCardsProps {
  stats: ProxyStats | undefined;
  filters: ProxyFilters;
  onFilter: (patch: Partial<ProxyFilters>) => void;
}

export function StatsCards({ stats, filters, onFilter }: StatsCardsProps) {
  const toggle = <K extends keyof ProxyFilters>(key: K, value: NonNullable<ProxyFilters[K]>) => {
    onFilter({ [key]: filters[key] === value ? undefined : value });
  };
  const alivePercent = stats && stats.total > 0 ? Math.round((stats.alive / stats.total) * 100) : 0;
  const iconClass = "size-4";

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Card
        icon={<Layers className={iconClass} aria-hidden />}
        title="Tổng proxy"
        value={stats?.total}
        unit="proxy"
        note={stats ? `${formatNumber(stats.enabled)} đang bật · ${stats.pools.length} pool` : "Đang tải…"}
      >
        <Chip
          label="Tĩnh"
          tone="slate"
          value={stats?.static}
          active={filters.kind === "static"}
          onClick={() => {
            toggle("kind", "static");
          }}
        />
        <Chip
          label="4G xoay"
          tone="violet"
          value={stats?.rotating}
          active={filters.kind === "rotating"}
          onClick={() => {
            toggle("kind", "rotating");
          }}
        />
      </Card>
      <Card
        icon={<Activity className={iconClass} aria-hidden />}
        title="Tình trạng"
        value={stats?.alive}
        unit="đang sống"
        note={stats ? `${alivePercent}% tổng số proxy` : "Đang tải…"}
      >
        <Chip
          label="Sống"
          tone="green"
          value={stats?.alive}
          active={filters.health === "alive"}
          onClick={() => {
            toggle("health", "alive");
          }}
        />
        <Chip
          label="Chết"
          tone="red"
          value={stats?.dead}
          active={filters.health === "dead"}
          onClick={() => {
            toggle("health", "dead");
          }}
        />
        <Chip
          label="Chưa kiểm tra"
          tone="slate"
          value={stats?.unchecked}
          active={filters.health === "unchecked"}
          onClick={() => {
            toggle("health", "unchecked");
          }}
        />
      </Card>
      <Card
        icon={<MonitorSmartphone className={iconClass} aria-hidden />}
        title="Cho máy PC thuê"
        value={stats?.leased_proxies}
        unit="proxy đang dùng"
        note={stats ? `${formatNumber(stats.active_leases)} lượt thuê đang mở` : "Đang tải…"}
      >
        <Chip
          label="Đang cho thuê"
          tone="blue"
          value={stats?.leased_proxies}
          active={filters.leased === true}
          onClick={() => {
            toggle("leased", true);
          }}
        />
        <Chip
          label="Bị cách ly"
          tone="amber"
          value={stats?.quarantined}
          active={filters.quarantined === true}
          onClick={() => {
            toggle("quarantined", true);
          }}
        />
      </Card>
      <Card
        icon={<RotateCw className={iconClass} aria-hidden />}
        title="Đổi IP 4G"
        value={stats?.rotating_now}
        unit="đang đổi IP"
        note={stats ? `${formatNumber(stats.rotation_pending)} proxy chờ máy trả để đổi IP` : "Đang tải…"}
      >
        <Chip
          label="Đang đổi"
          tone="blue"
          value={stats?.rotating_now}
          active={filters.rotation_state === "rotating"}
          onClick={() => {
            toggle("rotation_state", "rotating");
          }}
        />
        <Chip
          label="Chờ đổi"
          tone="amber"
          value={stats?.rotation_pending}
          active={filters.rotation_state === "pending"}
          onClick={() => {
            toggle("rotation_state", "pending");
          }}
        />
      </Card>
    </div>
  );
}
