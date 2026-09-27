import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { errorMessage } from "../../lib/api";
import { formatNumber } from "../../lib/format";
import { proxiesApi, useRefreshProxies } from "../../lib/proxies";
import { countFilters } from "../../lib/proxy-filters";
import type { Selection } from "../../lib/selection";
import type { BulkAction, BulkRequest, BulkResult, ProxyFilters } from "../../lib/types";
import { BulkBar } from "./BulkBar";
import { SetPoolDialog } from "./SetPoolDialog";

type ConfirmAction = "delete" | "rotate" | "reset_stats";

const CONFIRMS: Record<ConfirmAction, { title: string; confirmLabel: string; danger: boolean; body: string }> = {
  delete: {
    title: "Xoá",
    confirmLabel: "Xoá proxy",
    danger: true,
    body: "Các proxy này sẽ bị xoá vĩnh viễn khỏi kho, không thể hoàn tác. Máy PC đang thuê các proxy này sẽ không gia hạn được và phải thuê proxy khác.",
  },
  rotate: {
    title: "Đổi IP",
    confirmLabel: "Đổi IP",
    danger: false,
    body: "Chỉ proxy 4G có link đổi IP hoặc {session} được đổi. Proxy đang có máy PC thuê sẽ đổi IP khi máy trả proxy, proxy vừa đổi IP sẽ đổi khi hết thời gian chờ, proxy đang tắt sẽ đổi khi được bật lại.",
  },
  reset_stats: {
    title: "Đặt lại thống kê",
    confirmLabel: "Đặt lại thống kê",
    danger: false,
    body: "Số lần tốt, số lần lỗi và số lỗi liên tiếp sẽ về 0. Proxy đang bị cách ly sẽ hết cách ly và được cho thuê lại ngay nếu đang sống.",
  },
};

const ACTION_VERBS: Record<BulkAction, string> = {
  enable: "bật",
  disable: "tắt",
  delete: "xoá",
  check: "kiểm tra",
  rotate: "đổi IP",
  set_pool: "chuyển pool",
  reset_stats: "đặt lại thống kê",
};

const SKIP_REASONS: Record<BulkAction, string> = {
  enable: "đã bật từ trước",
  disable: "đã tắt từ trước",
  delete: "không còn trong kho",
  check: "đang được kiểm tra hoặc đang đổi IP",
  rotate: "là proxy tĩnh, chưa có link đổi IP hoặc {session}, hoặc đang chờ/đang đổi IP",
  set_pool: "đã ở pool này",
  reset_stats: "không còn trong kho",
};

function isConfirmAction(action: BulkAction): action is ConfirmAction {
  return action === "delete" || action === "rotate" || action === "reset_stats";
}

function showBulkToast(result: BulkResult): void {
  const skipped = result.matched - result.affected;
  const description =
    skipped > 0 ? `${formatNumber(skipped)} proxy bỏ qua vì ${SKIP_REASONS[result.action]}.` : undefined;
  if (result.affected > 0) {
    toast.success(result.message, { description });
  } else {
    toast.warning(result.message, { description });
  }
}

interface BulkActionsProps {
  selection: Selection | null;
  filters: ProxyFilters;
  count: number;
  pools: readonly string[];
  onClear: () => void;
}

export function BulkActions({ selection, filters, count, pools, onClear }: BulkActionsProps) {
  const refresh = useRefreshProxies();
  const [confirm, setConfirm] = useState<ConfirmAction | null>(null);
  const [choosingPool, setChoosingPool] = useState(false);

  const bulkMutation = useMutation({
    mutationFn: (request: BulkRequest) => proxiesApi.bulk(request),
    onSuccess: (result) => {
      showBulkToast(result);
      setConfirm(null);
      setChoosingPool(false);
      if (result.action === "delete" || result.action === "set_pool") {
        onClear();
      }
    },
    onError: (error, request) => {
      toast.error(`Chưa ${ACTION_VERBS[request.action]} được proxy`, { description: errorMessage(error) });
    },
    onSettled: refresh,
  });

  const allMatching = selection?.mode === "all";
  const everything = countFilters(filters) === 0;
  const amount = formatNumber(count);
  let label = `Đã chọn ${amount} proxy`;
  let scope = `${amount} proxy đã chọn`;
  if (allMatching) {
    label = everything ? `Tất cả ${amount} proxy trong kho` : `Tất cả ${amount} proxy khớp bộ lọc`;
    scope = everything ? `tất cả ${amount} proxy trong kho` : `tất cả ${amount} proxy khớp bộ lọc`;
  }

  const run = (action: BulkAction, pool?: string) => {
    if (selection === null) {
      return;
    }
    const target = selection.mode === "all" ? { filter: filters } : { ids: [...selection.ids] };
    bulkMutation.mutate({ action, ...target, pool });
  };

  const onAction = (action: BulkAction) => {
    bulkMutation.reset();
    if (action === "set_pool") {
      setChoosingPool(true);
    } else if (isConfirmAction(action)) {
      setConfirm(action);
    } else {
      run(action);
    }
  };

  return (
    <>
      {count > 0 ? (
        <BulkBar
          label={label}
          pending={bulkMutation.isPending ? bulkMutation.variables.action : null}
          onAction={onAction}
          onClear={onClear}
        />
      ) : null}
      {confirm ? (
        <ConfirmDialog
          open
          title={`${CONFIRMS[confirm].title} ${scope}?`}
          confirmLabel={CONFIRMS[confirm].confirmLabel}
          danger={CONFIRMS[confirm].danger}
          loading={bulkMutation.isPending}
          onConfirm={() => {
            run(confirm);
          }}
          onClose={() => {
            setConfirm(null);
          }}
        >
          <p>{CONFIRMS[confirm].body}</p>
          {allMatching ? (
            <p className="mt-2">
              Áp dụng cho mọi proxy {everything ? "trong kho" : "khớp bộ lọc"} lúc bấm xác nhận, kể cả những proxy chưa
              hiện trên trang đang xem.
            </p>
          ) : null}
        </ConfirmDialog>
      ) : null}
      {choosingPool ? (
        <SetPoolDialog
          count={count}
          pools={pools}
          loading={bulkMutation.isPending}
          onSubmit={(pool) => {
            run("set_pool", pool);
          }}
          onClose={() => {
            setChoosingPool(false);
          }}
        />
      ) : null}
    </>
  );
}
