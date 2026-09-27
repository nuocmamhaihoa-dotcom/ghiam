import {
  Eraser,
  FolderInput,
  Power,
  PowerOff,
  RefreshCw,
  RotateCw,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";

import { Spinner } from "../../components/ui/Spinner";
import { cn } from "../../lib/cn";
import type { BulkAction } from "../../lib/types";

const ACTIONS: readonly { action: BulkAction; label: string; icon: LucideIcon; danger?: boolean }[] = [
  { action: "check", label: "Kiểm tra", icon: RefreshCw },
  { action: "rotate", label: "Đổi IP", icon: RotateCw },
  { action: "enable", label: "Bật", icon: Power },
  { action: "disable", label: "Tắt", icon: PowerOff },
  { action: "set_pool", label: "Chuyển pool", icon: FolderInput },
  { action: "reset_stats", label: "Đặt lại thống kê", icon: Eraser },
  { action: "delete", label: "Xoá", icon: Trash2, danger: true },
];

interface BulkBarProps {
  label: string;
  pending: BulkAction | null;
  onAction: (action: BulkAction) => void;
  onClear: () => void;
}

export function BulkBar({ label, pending, onAction, onClear }: BulkBarProps) {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 lg:pl-68">
      <div
        role="toolbar"
        aria-label="Thao tác với proxy đã chọn"
        className="pointer-events-auto flex max-w-full flex-wrap items-center gap-1 rounded-xl bg-slate-900 p-1.5 text-sm text-white shadow-2xl ring-1 ring-white/10 motion-safe:animate-pop-in"
      >
        <span className="px-2.5 font-medium whitespace-nowrap tabular-nums">{label}</span>
        <span className="mx-1 hidden h-5 w-px bg-white/15 sm:block" aria-hidden />
        {ACTIONS.map(({ action, label, icon: Icon, danger }) => (
          <button
            key={action}
            type="button"
            disabled={pending !== null}
            onClick={() => {
              onAction(action);
            }}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              danger ? "text-rose-300 hover:bg-rose-500/20 hover:text-rose-200" : "text-slate-200 hover:bg-white/10 hover:text-white",
            )}
          >
            {pending === action ? <Spinner className="size-4" /> : <Icon className="size-4" aria-hidden />}
            {label}
          </button>
        ))}
        <button
          type="button"
          onClick={onClear}
          title="Bỏ chọn"
          aria-label="Bỏ chọn"
          className="ml-1 rounded-lg p-2 text-slate-400 transition-colors hover:bg-white/10 hover:text-white"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}
