import clsx from "clsx";
import { ChevronRight, PowerOff, RefreshCw, RotateCw, ShieldAlert } from "lucide-react";
import type { MouseEvent, ReactNode } from "react";

import { Badge } from "../../components/ui/Badge";
import { Checkbox } from "../../components/ui/form";
import { Spinner } from "../../components/ui/Spinner";
import { cn } from "../../lib/cn";
import {
  PROTOCOL_LABELS,
  cooldownLeft,
  describeRotation,
  formatLatency,
  formatNumber,
  formatRelative,
  formatRemaining,
} from "../../lib/format";
import { useNow } from "../../lib/hooks";
import { isRotatable } from "../../lib/proxies";
import type { PageSelection } from "../../lib/selection";
import type { ProxyItem } from "../../lib/types";
import { HealthBadge, KindBadge, RotationStateBadge } from "./badges";

interface ProxyTableProps {
  items: ProxyItem[];
  dimmed: boolean;
  isSelected: (id: number) => boolean;
  pageSelection: PageSelection;
  onToggle: (id: number, selected: boolean) => void;
  onTogglePage: (selected: boolean) => void;
  checkingIds: ReadonlySet<number>;
  rotatingIds: ReadonlySet<number>;
  onOpen: (id: number) => void;
  onCheck: (id: number) => void;
  onRotate: (id: number) => void;
}

function stop(event: MouseEvent) {
  event.stopPropagation();
}

interface IconButtonProps {
  label: string;
  onClick: () => void;
  loading?: boolean;
  children: ReactNode;
}

function IconButton({ label, onClick, loading = false, children }: IconButtonProps) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={loading}
      onClick={onClick}
      className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:cursor-wait"
    >
      {loading ? <Spinner className="size-4 text-indigo-500" /> : children}
    </button>
  );
}

function UsageBar({ used, max }: { used: number; max: number }) {
  const percent = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  return (
    <div className="mt-1 h-1.5 w-20 overflow-hidden rounded-full bg-slate-100">
      <div
        className={clsx("h-full rounded-full", percent >= 100 ? "bg-amber-500" : "bg-indigo-500")}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

const COLUMNS = ["Proxy", "Loại", "Tình trạng", "IP ra", "Đổi IP", "Sử dụng"] as const;

export function ProxyTableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="divide-y divide-slate-100" aria-busy="true" aria-label="Đang tải danh sách proxy">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex animate-pulse items-center gap-6 px-4 py-4">
          <div className="size-4 rounded bg-slate-200" />
          <div className="h-4 w-64 rounded bg-slate-200" />
          <div className="h-4 w-16 rounded bg-slate-100" />
          <div className="h-4 w-24 rounded bg-slate-100" />
          <div className="h-4 w-28 rounded bg-slate-100" />
          <div className="hidden h-4 w-32 rounded bg-slate-100 md:block" />
        </div>
      ))}
    </div>
  );
}

export function ProxyTable({
  items,
  dimmed,
  isSelected,
  pageSelection,
  onToggle,
  onTogglePage,
  checkingIds,
  rotatingIds,
  onOpen,
  onCheck,
  onRotate,
}: ProxyTableProps) {
  const now = useNow();
  return (
    <div className={clsx("overflow-x-auto transition-opacity", dimmed && "opacity-60")}>
      <table className="w-full min-w-[1080px] text-sm">
        <thead className="border-b border-slate-200 bg-slate-50/80 text-left text-xs font-medium text-slate-500">
          <tr>
            <th scope="col" className="w-10 py-2.5 pl-4">
              <Checkbox
                aria-label="Chọn tất cả proxy trên trang này"
                checked={pageSelection === "all"}
                ref={(element) => {
                  if (element) {
                    element.indeterminate = pageSelection === "some";
                  }
                }}
                onChange={(event) => {
                  onTogglePage(event.target.checked);
                }}
              />
            </th>
            {COLUMNS.map((column) => (
              <th key={column} scope="col" className="px-3 py-2.5 font-medium">
                {column}
              </th>
            ))}
            <th scope="col" className="py-2.5 pr-4">
              <span className="sr-only">Thao tác</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((proxy) => {
            const selected = isSelected(proxy.id);
            const checking = proxy.check_in_progress || checkingIds.has(proxy.id);
            const quarantineLeft = formatRemaining(proxy.quarantined_until, now);
            const waitSec = cooldownLeft(proxy, now);
            const location = [proxy.country, proxy.isp].filter(Boolean).join(" · ");
            return (
              <tr
                key={proxy.id}
                onClick={() => {
                  onOpen(proxy.id);
                }}
                className={cn(
                  "cursor-pointer align-top transition-colors hover:bg-slate-50",
                  selected && "bg-indigo-50/50 hover:bg-indigo-50",
                )}
              >
                <td className="py-3 pl-4" onClick={stop}>
                  <Checkbox
                    aria-label={`Chọn proxy #${proxy.id}`}
                    checked={selected}
                    onChange={(event) => {
                      onToggle(proxy.id, event.target.checked);
                    }}
                    className="mt-0.5"
                  />
                </td>
                <td className="max-w-80 px-3 py-3">
                  <button
                    type="button"
                    title={proxy.display}
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpen(proxy.id);
                    }}
                    className={clsx(
                      "block max-w-full truncate text-left font-mono text-[13px] hover:text-indigo-600 focus-visible:underline focus-visible:outline-none",
                      proxy.enabled ? "text-slate-900" : "text-slate-400 line-through",
                    )}
                  >
                    {proxy.display}
                  </button>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                    <span className="tabular-nums">#{proxy.id}</span>
                    <Badge tone="indigo">{proxy.pool}</Badge>
                    {proxy.enabled ? null : (
                      <Badge tone="gray" icon={<PowerOff className="size-3" aria-hidden />}>
                        Đang tắt
                      </Badge>
                    )}
                    {proxy.note ? (
                      <span className="max-w-44 truncate" title={proxy.note}>
                        {proxy.note}
                      </span>
                    ) : null}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <KindBadge kind={proxy.kind} />
                  <div className="mt-1 text-xs text-slate-500">{PROTOCOL_LABELS[proxy.protocol]}</div>
                </td>
                <td className="px-3 py-3">
                  <HealthBadge health={proxy.health} checking={checking} />
                  <div className="mt-1 text-xs whitespace-nowrap text-slate-500">
                    {proxy.last_checked_at
                      ? `${formatLatency(proxy.latency_ms)} · ${formatRelative(proxy.last_checked_at, now)}`
                      : "Chưa kiểm tra lần nào"}
                  </div>
                  {proxy.health === "dead" && proxy.last_check_error ? (
                    <div className="mt-0.5 max-w-52 truncate text-xs text-rose-600" title={proxy.last_check_error}>
                      {proxy.last_check_error}
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-3">
                  {proxy.exit_ip ? (
                    <span className="font-mono text-[13px] text-slate-800">{proxy.exit_ip}</span>
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                  {location ? (
                    <div className="mt-1 max-w-44 truncate text-xs text-slate-500" title={location}>
                      {location}
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-3">
                  {proxy.kind === "rotating" ? (
                    <>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <RotationStateBadge state={proxy.rotation_state} />
                        {waitSec > 0 && proxy.rotation_state === "idle" ? (
                          <span
                            className="text-xs text-slate-500 tabular-nums"
                            title="Thời gian chờ tối thiểu giữa 2 lần đổi IP"
                          >
                            chờ {waitSec}s
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-1 max-w-56 truncate text-xs text-slate-500" title={describeRotation(proxy)}>
                        {describeRotation(proxy)}
                      </div>
                      <div className="text-xs text-slate-500">
                        {proxy.last_rotated_at
                          ? `Đổi IP ${formatRelative(proxy.last_rotated_at, now)} · ${formatNumber(proxy.rotation_count)} lần`
                          : "Chưa đổi IP lần nào"}
                      </div>
                      {proxy.last_rotation_ok === false && proxy.last_rotation_message ? (
                        <div
                          className="mt-0.5 max-w-56 truncate text-xs text-rose-600"
                          title={proxy.last_rotation_message}
                        >
                          {proxy.last_rotation_message}
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <span className="text-xs text-slate-400">IP cố định</span>
                  )}
                </td>
                <td className="px-3 py-3">
                  <div className="text-sm whitespace-nowrap text-slate-800 tabular-nums">
                    {proxy.active_leases}/{proxy.max_concurrency} máy
                  </div>
                  <UsageBar used={proxy.active_leases} max={proxy.max_concurrency} />
                  {quarantineLeft ? (
                    <div className="mt-1.5">
                      <Badge
                        tone="amber"
                        icon={<ShieldAlert className="size-3" aria-hidden />}
                        title={`Lỗi ${proxy.consecutive_failures} lần liên tiếp nên tạm ngừng cho thuê`}
                      >
                        Cách ly {quarantineLeft}
                      </Badge>
                    </div>
                  ) : null}
                  <div className="mt-1 text-xs whitespace-nowrap text-slate-500">
                    Tốt {formatNumber(proxy.success_count)} · Lỗi {formatNumber(proxy.failure_count)}
                  </div>
                </td>
                <td className="py-3 pr-4" onClick={stop}>
                  <div className="flex justify-end gap-0.5">
                    <IconButton
                      label="Kiểm tra sống/chết ngay"
                      loading={checking}
                      onClick={() => {
                        onCheck(proxy.id);
                      }}
                    >
                      <RefreshCw className="size-4" aria-hidden />
                    </IconButton>
                    {isRotatable(proxy) ? (
                      <IconButton
                        label="Đổi IP ngay"
                        loading={rotatingIds.has(proxy.id)}
                        onClick={() => {
                          onRotate(proxy.id);
                        }}
                      >
                        <RotateCw className="size-4" aria-hidden />
                      </IconButton>
                    ) : null}
                    <IconButton
                      label="Xem chi tiết"
                      onClick={() => {
                        onOpen(proxy.id);
                      }}
                    >
                      <ChevronRight className="size-4" aria-hidden />
                    </IconButton>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
