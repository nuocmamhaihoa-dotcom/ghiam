import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Download, Globe, Plus, RefreshCw, SearchX, TriangleAlert } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { errorMessage } from "../lib/api";
import { formatNumber } from "../lib/format";
import { pageCountOf } from "../lib/pagination";
import {
  CHECK_MUTATION_KEY,
  ROTATE_MUTATION_KEY,
  proxiesApi,
  proxyKeys,
  usePendingIds,
  useProxyList,
  useProxyStats,
  useRefreshProxies,
} from "../lib/proxies";
import { countFilters, filtersOf, paramsFromSearch, searchFromParams } from "../lib/proxy-filters";
import {
  isSelected,
  pageSelectionOf,
  selectedCount,
  toggleOne,
  togglePage,
  type Selection,
} from "../lib/selection";
import type { ProxyListParams } from "../lib/types";
import { BulkActions } from "./proxies/BulkActions";
import { ExportDialog } from "./proxies/ExportDialog";
import { ImportDrawer } from "./proxies/ImportDrawer";
import { notifyCheck, notifyRotate } from "./proxies/notify";
import { Pagination } from "./proxies/Pagination";
import { ProxyDetailDrawer } from "./proxies/ProxyDetailDrawer";
import { ProxyTable, ProxyTableSkeleton } from "./proxies/ProxyTable";
import { ProxyToolbar } from "./proxies/ProxyToolbar";
import { StatsCards } from "./proxies/StatsCards";

/** Lựa chọn gắn với bộ lọc lúc chọn: đổi bộ lọc là bỏ chọn, tránh thao tác nhầm lên proxy không còn hiện. */
interface StoredSelection {
  filterKey: string;
  value: Selection;
}

function Banner({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 border-b border-indigo-100 bg-indigo-50 px-4 py-2 text-sm text-indigo-900">
      {children}
    </div>
  );
}

function BannerButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="font-medium text-indigo-700 underline-offset-2 hover:text-indigo-900 hover:underline"
    >
      {children}
    </button>
  );
}

export function ProxiesPage() {
  const queryClient = useQueryClient();
  const refresh = useRefreshProxies();
  const [searchParams, setSearchParams] = useSearchParams();
  const params = useMemo(() => paramsFromSearch(searchParams), [searchParams]);
  const filters = useMemo(() => filtersOf(params), [params]);
  const filterKey = JSON.stringify(filters);
  const hasFilters = countFilters(filters) > 0;

  const list = useProxyList(params);
  const stats = useProxyStats();
  const checkingIds = usePendingIds(CHECK_MUTATION_KEY);
  const rotatingIds = usePendingIds(ROTATE_MUTATION_KEY);

  const [stored, setStored] = useState<StoredSelection | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const cardRef = useRef<HTMLElement>(null);

  const data = list.data;
  const items = data?.items ?? [];
  const pageIds = items.map((proxy) => proxy.id);
  const total = data?.total ?? 0;
  const selection = stored?.filterKey === filterKey ? stored.value : null;
  const pageSelection = pageSelectionOf(selection, pageIds);
  const poolNames = (stats.data?.pools ?? []).map((item) => item.pool);

  const checkMutation = useMutation({
    mutationKey: CHECK_MUTATION_KEY,
    mutationFn: (id: number) => proxiesApi.check(id),
    onSuccess: (result, id) => {
      notifyCheck(id, result);
    },
    onError: (error, id) => {
      toast.error(`Không kiểm tra được proxy #${id}`, { description: errorMessage(error) });
    },
    onSettled: refresh,
  });

  const rotateMutation = useMutation({
    mutationKey: ROTATE_MUTATION_KEY,
    mutationFn: (id: number) => proxiesApi.rotate(id, { wait: false }),
    onSuccess: (result, id) => {
      notifyRotate(id, result, refresh);
    },
    onError: (error, id) => {
      toast.error(`Không đổi IP được proxy #${id}`, { description: errorMessage(error) });
    },
    onSettled: refresh,
  });

  const updateSelection = (change: (current: Selection | null) => Selection | null) => {
    setStored((current) => {
      const value = change(current?.filterKey === filterKey ? current.value : null);
      return value === null ? null : { filterKey, value };
    });
  };

  const clearSelection = () => {
    setStored(null);
  };

  const updateParams = (patch: Partial<ProxyListParams>) => {
    const next = { ...params, ...patch, page: patch.page ?? 1 };
    if (JSON.stringify(filtersOf(next)) !== filterKey) {
      setStored(null);
    }
    setSearchParams(searchFromParams(next), { replace: true });
  };

  const clearFilters = () => {
    setStored(null);
    setSearchParams(searchFromParams({ sort: params.sort, page: 1, page_size: params.page_size }), { replace: true });
  };

  const goToPage = (page: number) => {
    updateParams({ page });
    const card = cardRef.current;
    if (card && card.getBoundingClientRect().top < 0) {
      card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  async function refreshNow() {
    setRefreshing(true);
    try {
      await queryClient.refetchQueries({ queryKey: proxyKeys.all, type: "active" });
    } finally {
      setRefreshing(false);
    }
  }

  const openImport = () => {
    setImportOpen(true);
  };

  const everything = hasFilters ? "khớp bộ lọc" : "trong kho";
  let banner: ReactNode = null;
  if (selection?.mode === "all") {
    banner = (
      <Banner>
        <span>
          Đang chọn tất cả <strong className="tabular-nums">{formatNumber(total)}</strong> proxy {everything}.
        </span>
        <BannerButton onClick={clearSelection}>Bỏ chọn</BannerButton>
      </Banner>
    );
  } else if (pageSelection === "all" && total > pageIds.length && !list.isPlaceholderData) {
    banner = (
      <Banner>
        <span>
          Đã chọn <strong className="tabular-nums">{formatNumber(pageIds.length)}</strong> proxy trên trang này.
        </span>
        <BannerButton
          onClick={() => {
            setStored({ filterKey, value: { mode: "all" } });
          }}
        >
          Chọn tất cả {formatNumber(total)} proxy {everything}
        </BannerButton>
      </Banner>
    );
  }

  let content: ReactNode;
  if (!data) {
    content = list.isError ? (
      <EmptyState
        icon={<CircleAlert className="size-6" aria-hidden />}
        title="Chưa tải được danh sách proxy"
        description={errorMessage(list.error)}
        action={
          <Button
            onClick={() => {
              void list.refetch();
            }}
          >
            Thử lại
          </Button>
        }
      />
    ) : (
      <ProxyTableSkeleton />
    );
  } else if (data.total === 0 && hasFilters) {
    content = (
      <EmptyState
        icon={<SearchX className="size-6" aria-hidden />}
        title="Không có proxy nào khớp bộ lọc"
        description="Thử bỏ bớt điều kiện lọc hoặc đổi từ khoá tìm kiếm."
        action={<Button onClick={clearFilters}>Xoá lọc</Button>}
      />
    );
  } else if (data.total === 0) {
    content = (
      <EmptyState
        icon={<Globe className="size-6" aria-hidden />}
        title="Kho proxy đang trống"
        description="Dán danh sách proxy tĩnh hoặc proxy 4G kèm link đổi IP, mọi định dạng phổ biến đều được. Bạn sẽ xem trước từng dòng trước khi nhập."
        action={
          <Button variant="primary" icon={<Plus className="size-4" aria-hidden />} onClick={openImport}>
            Thêm proxy hàng loạt
          </Button>
        }
      />
    );
  } else if (items.length === 0) {
    const lastPage = pageCountOf(data.total, data.page_size);
    content = (
      <EmptyState
        icon={<SearchX className="size-6" aria-hidden />}
        title="Trang này không còn proxy nào"
        description={`Danh sách hiện chỉ có ${formatNumber(lastPage)} trang.`}
        action={
          <Button
            onClick={() => {
              goToPage(lastPage);
            }}
          >
            Về trang {formatNumber(lastPage)}
          </Button>
        }
      />
    );
  } else {
    content = (
      <ProxyTable
        items={items}
        dimmed={list.isPlaceholderData}
        isSelected={(id) => isSelected(selection, id)}
        pageSelection={pageSelection}
        onToggle={(id, selected) => {
          updateSelection((current) => toggleOne(current, id, selected, pageIds));
        }}
        onTogglePage={(selected) => {
          updateSelection((current) => togglePage(current, pageIds, selected));
        }}
        checkingIds={checkingIds}
        rotatingIds={rotatingIds}
        onOpen={setDetailId}
        onCheck={(id) => {
          checkMutation.mutate(id);
        }}
        onRotate={(id) => {
          rotateMutation.mutate(id);
        }}
      />
    );
  }

  const count = selectedCount(selection, total);

  return (
    <div className={count > 0 ? "space-y-6 pb-36 sm:pb-24" : "space-y-6"}>
      <title>Kho proxy · CommentScope</title>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-slate-900">Kho proxy</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">
            Thêm proxy tĩnh và proxy 4G xoay hàng loạt. Hệ thống tự kiểm tra sống/chết, đổi IP proxy 4G và cho các máy
            PC thuê proxy khi mở trình duyệt quét comment.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            icon={<RefreshCw className="size-4" aria-hidden />}
            loading={refreshing}
            onClick={() => {
              void refreshNow();
            }}
          >
            Làm mới
          </Button>
          <Button
            icon={<Download className="size-4" aria-hidden />}
            onClick={() => {
              setExportOpen(true);
            }}
          >
            Xuất file
          </Button>
          <Button variant="primary" icon={<Plus className="size-4" aria-hidden />} onClick={openImport}>
            Thêm proxy hàng loạt
          </Button>
        </div>
      </div>

      <StatsCards stats={stats.data} filters={filters} onFilter={updateParams} />

      <section
        ref={cardRef}
        aria-label="Danh sách proxy"
        className="scroll-mt-20 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-slate-200 lg:scroll-mt-6"
      >
        <div className="border-b border-slate-200 p-4">
          <ProxyToolbar
            params={params}
            pools={stats.data?.pools ?? []}
            onChange={updateParams}
            onClear={clearFilters}
          />
        </div>
        {banner}
        {data && list.isError ? (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900"
          >
            <TriangleAlert className="size-4 shrink-0 text-amber-600" aria-hidden />
            <span className="min-w-0 flex-1">
              Chưa làm mới được danh sách, đang hiện dữ liệu cũ. {errorMessage(list.error)}
            </span>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                void list.refetch();
              }}
            >
              Thử lại
            </Button>
          </div>
        ) : null}
        {content}
        {data && data.total > 0 ? (
          <Pagination
            page={params.page}
            pageSize={params.page_size}
            total={data.total}
            onPage={goToPage}
            onPageSize={(pageSize) => {
              updateParams({ page_size: pageSize });
            }}
          />
        ) : null}
      </section>

      <BulkActions
        selection={selection}
        filters={filters}
        count={count}
        pools={poolNames}
        onClear={clearSelection}
      />
      <ImportDrawer
        open={importOpen}
        onClose={() => {
          setImportOpen(false);
        }}
        pools={poolNames}
      />
      <ProxyDetailDrawer
        proxyId={detailId}
        pools={poolNames}
        onClose={() => {
          setDetailId(null);
        }}
      />
      {exportOpen ? (
        <ExportDialog
          filters={filters}
          filteredCount={list.isPlaceholderData ? undefined : data?.total}
          totalCount={stats.data?.total}
          onClose={() => {
            setExportOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}
