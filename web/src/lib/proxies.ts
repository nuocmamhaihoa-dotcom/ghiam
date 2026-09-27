import { keepPreviousData, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { api } from "./api";
import type {
  BulkRequest,
  BulkResult,
  CheckResult,
  ExportFormat,
  ImportPreview,
  ImportPreviewRequest,
  ImportRequest,
  ImportResult,
  ProxyDetail,
  ProxyFilters,
  ProxyItem,
  ProxyList,
  ProxyListParams,
  ProxyStats,
  ProxyUpdate,
  RotateResult,
} from "./types";

export const proxyKeys = {
  all: ["proxies"] as const,
  list: (params: ProxyListParams) => ["proxies", "list", params] as const,
  stats: ["proxies", "stats"] as const,
  detail: (id: number) => ["proxies", "detail", id] as const,
};

/** Bảng và ngăn chi tiết dùng chung khoá để hàng trong bảng hiện vòng quay dù thao tác bấm ở đâu. */
export const CHECK_MUTATION_KEY = ["proxy-check"] as const;
export const ROTATE_MUTATION_KEY = ["proxy-rotate"] as const;

export interface RotateVars {
  id: number;
  force: boolean;
}

function variablesId(variables: unknown): number | undefined {
  if (typeof variables === "number") {
    return variables;
  }
  if (typeof variables === "object" && variables !== null && "id" in variables && typeof variables.id === "number") {
    return variables.id;
  }
  return undefined;
}

/** Id các proxy đang có thao tác (kiểm tra, đổi IP...) chưa xong theo khoá mutation. */
export function usePendingIds(mutationKey: readonly string[]): ReadonlySet<number> {
  const ids = useMutationState({
    filters: { mutationKey, status: "pending" },
    select: (mutation) => variablesId(mutation.state.variables),
  });
  return useMemo(() => new Set(ids.filter((id) => id !== undefined)), [ids]);
}

export interface ExportQuery extends ProxyFilters {
  format: ExportFormat;
  with_options: boolean;
}

export const proxiesApi = {
  list: (params: ProxyListParams, signal?: AbortSignal) => api.get<ProxyList>("/api/proxies", { query: params, signal }),
  stats: (signal?: AbortSignal) => api.get<ProxyStats>("/api/proxies/stats", { signal }),
  detail: (id: number, signal?: AbortSignal) => api.get<ProxyDetail>(`/api/proxies/${id}`, { signal }),
  preview: (body: ImportPreviewRequest, signal?: AbortSignal) =>
    api.post<ImportPreview>("/api/proxies/import/preview", body, { signal }),
  import: (body: ImportRequest) => api.post<ImportResult>("/api/proxies/import", body),
  bulk: (body: BulkRequest) => api.post<BulkResult>("/api/proxies/bulk", body),
  update: (id: number, body: ProxyUpdate) => api.patch<ProxyDetail>(`/api/proxies/${id}`, body),
  remove: (id: number) => api.delete(`/api/proxies/${id}`),
  check: (id: number) => api.post<CheckResult>(`/api/proxies/${id}/check`),
  rotate: (id: number, options: { force?: boolean; wait?: boolean } = {}) =>
    api.post<RotateResult>(`/api/proxies/${id}/rotate`, undefined, { query: options }),
  export: (query: ExportQuery) => api.download("/api/proxies/export", query),
};

const FAST_REFRESH_MS = 2000;
const NORMAL_REFRESH_MS = 10_000;
const SLOW_REFRESH_MS = 15_000;

/** Proxy đang được kiểm tra hoặc đang/chờ đổi IP: cần làm mới dữ liệu nhanh hơn. */
export function isBusy(proxy: ProxyItem): boolean {
  return proxy.check_in_progress || proxy.rotation_state !== "idle";
}

export function isRotatable(proxy: ProxyItem): boolean {
  return proxy.rotation_mode === "url" || proxy.rotation_mode === "session";
}

export function useProxyList(params: ProxyListParams) {
  return useQuery({
    queryKey: proxyKeys.list(params),
    queryFn: ({ signal }) => proxiesApi.list(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.items.some(isBusy) ? FAST_REFRESH_MS : SLOW_REFRESH_MS),
  });
}

export function useProxyStats() {
  return useQuery({
    queryKey: proxyKeys.stats,
    queryFn: ({ signal }) => proxiesApi.stats(signal),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.rotating_now + data.rotation_pending > 0 ? FAST_REFRESH_MS : NORMAL_REFRESH_MS;
    },
  });
}

export function useProxyDetail(id: number | null) {
  return useQuery({
    queryKey: proxyKeys.detail(id ?? 0),
    queryFn: ({ signal }) => proxiesApi.detail(id ?? 0, signal),
    enabled: id !== null,
    refetchInterval: (query) => (query.state.data && isBusy(query.state.data) ? FAST_REFRESH_MS : NORMAL_REFRESH_MS),
  });
}

/** Làm mới mọi dữ liệu proxy (danh sách, thống kê, chi tiết) sau khi thay đổi. */
export function useRefreshProxies(): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: proxyKeys.all });
  }, [queryClient]);
}
