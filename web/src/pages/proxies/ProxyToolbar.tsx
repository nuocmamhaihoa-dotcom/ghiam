import clsx from "clsx";
import { ArrowDownUp, Search, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Select } from "../../components/ui/form";
import { SORT_LABELS, formatNumber } from "../../lib/format";
import { SORTS, countFilters } from "../../lib/proxy-filters";
import type { ProxyListParams, ProxySort, ProxyStats } from "../../lib/types";

const SEARCH_DELAY_MS = 350;

function SearchBox({ value, onSearch }: { value: string | undefined; onSearch: (q: string | undefined) => void }) {
  const [text, setText] = useState(value ?? "");
  const [syncedValue, setSyncedValue] = useState(value);
  const timer = useRef<number | undefined>(undefined);

  if (value !== syncedValue) {
    setSyncedValue(value);
    setText(value ?? "");
  }

  useEffect(() => {
    const pending = timer;
    return () => {
      window.clearTimeout(pending.current);
    };
  }, []);

  const schedule = (next: string, delayMs: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      onSearch(next.trim() || undefined);
    }, delayMs);
  };

  return (
    <div className="relative min-w-56 flex-1 sm:max-w-sm">
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" aria-hidden />
      <input
        type="search"
        value={text}
        placeholder="Tìm host, IP ra, user, pool, ghi chú, host:port…"
        aria-label="Tìm proxy"
        onChange={(event) => {
          setText(event.target.value);
          schedule(event.target.value, SEARCH_DELAY_MS);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            schedule(text, 0);
          }
        }}
        className="block h-9 w-full rounded-lg border-0 bg-white pr-8 pl-9 text-sm text-slate-900 shadow-sm ring-1 ring-slate-300 ring-inset placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-600 focus:outline-none focus:ring-inset [&::-webkit-search-cancel-button]:hidden"
      />
      {text ? (
        <button
          type="button"
          aria-label="Xoá từ khoá"
          onClick={() => {
            setText("");
            schedule("", 0);
          }}
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:text-slate-600"
        >
          <X className="size-4" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

interface Option {
  value: string;
  label: string;
}

interface FilterSelectProps {
  label: string;
  value: string | undefined;
  allLabel: string;
  options: readonly Option[];
  onChange: (value: string | undefined) => void;
}

function FilterSelect({ label, value, allLabel, options, onChange }: FilterSelectProps) {
  return (
    <Select
      aria-label={label}
      title={label}
      value={value ?? ""}
      onChange={(event) => {
        onChange(event.target.value || undefined);
      }}
      className={clsx("w-auto max-w-52", value !== undefined && "bg-indigo-50/60 ring-indigo-300")}
    >
      <option value="">{allLabel}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </Select>
  );
}

function boolValue(value: boolean | undefined): string | undefined {
  return value === undefined ? undefined : String(value);
}

function parseBool(value: string | undefined): boolean | undefined {
  return value === undefined ? undefined : value === "true";
}

interface ProxyToolbarProps {
  params: ProxyListParams;
  pools: ProxyStats["pools"];
  onChange: (patch: Partial<ProxyListParams>) => void;
  onClear: () => void;
}

export function ProxyToolbar({ params, pools, onChange, onClear }: ProxyToolbarProps) {
  const extraCount = [params.enabled, params.leased, params.rotation_state, params.quarantined].filter(
    (value) => value !== undefined,
  ).length;
  const [moreVisible, setMoreVisible] = useState(() => extraCount > 0);
  const activeCount = countFilters(params);
  const poolOptions = pools.map((item) => ({ value: item.pool, label: `${item.pool} (${formatNumber(item.total)})` }));
  if (params.pool && !pools.some((item) => item.pool === params.pool)) {
    poolOptions.push({ value: params.pool, label: params.pool });
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <SearchBox
          value={params.q}
          onSearch={(q) => {
            onChange({ q });
          }}
        />
        <FilterSelect
          label="Loại proxy"
          allLabel="Mọi loại"
          value={params.kind}
          options={[
            { value: "static", label: "Proxy tĩnh" },
            { value: "rotating", label: "Proxy 4G xoay" },
          ]}
          onChange={(value) => {
            onChange({ kind: value as ProxyListParams["kind"] });
          }}
        />
        <FilterSelect
          label="Tình trạng"
          allLabel="Mọi tình trạng"
          value={params.health}
          options={[
            { value: "alive", label: "Đang sống" },
            { value: "dead", label: "Đã chết" },
            { value: "unchecked", label: "Chưa kiểm tra" },
          ]}
          onChange={(value) => {
            onChange({ health: value as ProxyListParams["health"] });
          }}
        />
        <FilterSelect
          label="Pool"
          allLabel="Mọi pool"
          value={params.pool}
          options={poolOptions}
          onChange={(value) => {
            onChange({ pool: value });
          }}
        />
        <Button
          size="md"
          variant={moreVisible || extraCount > 0 ? "subtle" : "secondary"}
          icon={<SlidersHorizontal className="size-4" aria-hidden />}
          onClick={() => {
            setMoreVisible((value) => !value);
          }}
          aria-expanded={moreVisible}
        >
          Lọc thêm{extraCount > 0 ? ` (${extraCount})` : ""}
        </Button>
        <div className="flex items-center gap-2 sm:ml-auto">
          <ArrowDownUp className="size-4 text-slate-400" aria-hidden />
          <Select
            aria-label="Sắp xếp"
            value={params.sort}
            onChange={(event) => {
              onChange({ sort: event.target.value as ProxySort });
            }}
            className="w-auto"
          >
            {SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {SORT_LABELS[sort]}
              </option>
            ))}
          </Select>
        </div>
        {activeCount > 0 ? (
          <Button variant="ghost" icon={<X className="size-4" aria-hidden />} onClick={onClear}>
            Xoá lọc ({activeCount})
          </Button>
        ) : null}
      </div>
      {moreVisible ? (
        <div className="flex flex-wrap items-center gap-2">
          <FilterSelect
            label="Bật/tắt"
            allLabel="Cả bật và tắt"
            value={boolValue(params.enabled)}
            options={[
              { value: "true", label: "Đang bật" },
              { value: "false", label: "Đang tắt" },
            ]}
            onChange={(value) => {
              onChange({ enabled: parseBool(value) });
            }}
          />
          <FilterSelect
            label="Cho thuê"
            allLabel="Thuê: tất cả"
            value={boolValue(params.leased)}
            options={[
              { value: "true", label: "Đang cho máy PC thuê" },
              { value: "false", label: "Đang rảnh" },
            ]}
            onChange={(value) => {
              onChange({ leased: parseBool(value) });
            }}
          />
          <FilterSelect
            label="Trạng thái đổi IP"
            allLabel="Đổi IP: tất cả"
            value={params.rotation_state}
            options={[
              { value: "idle", label: "Sẵn sàng" },
              { value: "pending", label: "Chờ đổi IP" },
              { value: "rotating", label: "Đang đổi IP" },
            ]}
            onChange={(value) => {
              onChange({ rotation_state: value as ProxyListParams["rotation_state"] });
            }}
          />
          <FilterSelect
            label="Cách ly"
            allLabel="Cách ly: tất cả"
            value={boolValue(params.quarantined)}
            options={[
              { value: "true", label: "Đang bị cách ly" },
              { value: "false", label: "Không bị cách ly" },
            ]}
            onChange={(value) => {
              onChange({ quarantined: parseBool(value) });
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
