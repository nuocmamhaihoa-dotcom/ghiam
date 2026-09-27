import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { Select } from "../../components/ui/form";
import { cn } from "../../lib/cn";
import { formatNumber } from "../../lib/format";
import { pageCountOf, pageWindow } from "../../lib/pagination";
import { PAGE_SIZES } from "../../lib/proxy-filters";

interface PageButtonProps {
  label: string;
  current?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}

function PageButton({ label, current = false, disabled = false, onClick, children }: PageButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-current={current ? "page" : undefined}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex h-8 min-w-8 items-center justify-center rounded-md px-2 text-sm font-medium tabular-nums transition-colors disabled:cursor-not-allowed disabled:text-slate-300",
        current ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100 disabled:hover:bg-transparent",
      )}
    >
      {children}
    </button>
  );
}

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  onPageSize: (pageSize: number) => void;
}

export function Pagination({ page, pageSize, total, onPage, onPageSize }: PaginationProps) {
  const pageCount = pageCountOf(total, pageSize);
  const current = Math.min(page, pageCount);
  const from = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const to = Math.min(total, current * pageSize);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-sm text-slate-600">
      <p>
        Hiển thị{" "}
        <span className="font-medium text-slate-900 tabular-nums">
          {formatNumber(from)}–{formatNumber(to)}
        </span>{" "}
        trên <span className="font-medium text-slate-900 tabular-nums">{formatNumber(total)}</span> proxy
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Select
          aria-label="Số proxy mỗi trang"
          value={pageSize}
          onChange={(event) => {
            onPageSize(Number(event.target.value));
          }}
          className="h-8 w-auto"
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size} / trang
            </option>
          ))}
        </Select>
        {pageCount > 1 ? (
          <nav aria-label="Phân trang" className="flex items-center gap-0.5">
            <PageButton
              label="Trang trước"
              disabled={current <= 1}
              onClick={() => {
                onPage(current - 1);
              }}
            >
              <ChevronLeft className="size-4" aria-hidden />
            </PageButton>
            {pageWindow(current, pageCount).map((item, index) =>
              item === null ? (
                <span key={`gap-${index}`} className="px-1 text-slate-400" aria-hidden>
                  …
                </span>
              ) : (
                <PageButton
                  key={item}
                  label={`Trang ${item}`}
                  current={item === current}
                  onClick={() => {
                    onPage(item);
                  }}
                >
                  {formatNumber(item)}
                </PageButton>
              ),
            )}
            <PageButton
              label="Trang sau"
              disabled={current >= pageCount}
              onClick={() => {
                onPage(current + 1);
              }}
            >
              <ChevronRight className="size-4" aria-hidden />
            </PageButton>
          </nav>
        ) : null}
      </div>
    </div>
  );
}
