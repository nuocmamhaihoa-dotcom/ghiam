import type { ProxyFilters, ProxyHealth, ProxyKind, ProxyListParams, ProxySort, RotationState } from "./types";

export const PAGE_SIZES = [25, 50, 100, 200] as const;
export const DEFAULT_PAGE_SIZE = 50;
export const DEFAULT_SORT: ProxySort = "-id";

export const SORTS: readonly ProxySort[] = [
  "-id",
  "id",
  "latency",
  "-latency",
  "-last_checked",
  "last_checked",
  "last_used",
  "host",
];
const KINDS: readonly ProxyKind[] = ["static", "rotating"];
const HEALTHS: readonly ProxyHealth[] = ["alive", "dead", "unchecked"];
const ROTATION_STATES: readonly RotationState[] = ["idle", "pending", "rotating"];
const FILTER_KEYS = [
  "q",
  "kind",
  "health",
  "pool",
  "enabled",
  "leased",
  "rotation_state",
  "quarantined",
] as const satisfies readonly (keyof ProxyFilters)[];

function pick<T extends string>(value: string | null, allowed: readonly T[]): T | undefined {
  return allowed.find((item) => item === value);
}

function parseBool(value: string | null): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

function parsePositiveInt(value: string | null): number | undefined {
  if (value === null || !/^\d{1,9}$/.test(value)) {
    return undefined;
  }
  const number = Number(value);
  return number >= 1 ? number : undefined;
}

export function paramsFromSearch(search: URLSearchParams): ProxyListParams {
  const pageSize = parsePositiveInt(search.get("page_size"));
  const filters = filtersOf({
    q: search.get("q")?.trim(),
    kind: pick(search.get("kind"), KINDS),
    health: pick(search.get("health"), HEALTHS),
    pool: search.get("pool")?.trim(),
    enabled: parseBool(search.get("enabled")),
    leased: parseBool(search.get("leased")),
    rotation_state: pick(search.get("rotation_state"), ROTATION_STATES),
    quarantined: parseBool(search.get("quarantined")),
  });
  return {
    ...filters,
    sort: pick(search.get("sort"), SORTS) ?? DEFAULT_SORT,
    page: parsePositiveInt(search.get("page")) ?? 1,
    page_size: PAGE_SIZES.find((size) => size === pageSize) ?? DEFAULT_PAGE_SIZE,
  };
}

export function searchFromParams(params: ProxyListParams): URLSearchParams {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(filtersOf(params))) {
    search.set(key, String(value));
  }
  if (params.sort !== DEFAULT_SORT) search.set("sort", params.sort);
  if (params.page !== 1) search.set("page", String(params.page));
  if (params.page_size !== DEFAULT_PAGE_SIZE) search.set("page_size", String(params.page_size));
  return search;
}

/** Chỉ giữ các điều kiện lọc có giá trị (bỏ phân trang, sắp xếp, chuỗi rỗng). */
export function filtersOf(params: ProxyFilters): ProxyFilters {
  return Object.fromEntries(
    FILTER_KEYS.flatMap((key) => {
      const value = params[key];
      return value === undefined || value === "" ? [] : [[key, value]];
    }),
  );
}

export function countFilters(filters: ProxyFilters): number {
  return Object.keys(filtersOf(filters)).length;
}
