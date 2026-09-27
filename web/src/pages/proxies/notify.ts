import { toast } from "sonner";

import { errorMessage } from "../../lib/api";
import { formatLatency } from "../../lib/format";
import { isRotatable, proxiesApi } from "../../lib/proxies";
import type { CheckResult, RotateResult } from "../../lib/types";

export function notifyCheck(id: number, result: CheckResult): void {
  if (!result.ok) {
    toast.error(`Proxy #${id} không dùng được`, { description: result.error ?? undefined });
    return;
  }
  const details = [
    result.latency_ms === null ? null : formatLatency(result.latency_ms),
    result.exit_ip,
    result.country,
  ].filter(Boolean);
  toast.success(`Proxy #${id} đang sống`, { description: details.length ? details.join(" · ") : undefined });
}

function skippedByCooldown(result: RotateResult): boolean {
  const proxy = result.proxy;
  return (
    result.status === "skipped" &&
    proxy !== null &&
    isRotatable(proxy) &&
    proxy.rotation_state !== "rotating" &&
    proxy.cooldown_remaining_sec > 0
  );
}

/**
 * Báo kết quả yêu cầu đổi IP. Có `refresh` thì toast kèm nút đổi IP bắt buộc (bỏ qua thời gian chờ
 * và máy đang thuê) cho các trường hợp máy chủ hoãn hoặc từ chối vì lý do đó.
 */
export function notifyRotate(id: number, result: RotateResult, refresh?: () => void): void {
  const force = refresh
    ? (label: string) => ({
        label,
        onClick: () => {
          void forceRotate(id, refresh);
        },
      })
    : undefined;
  switch (result.status) {
    case "rotated":
      toast.success(`Proxy #${id} đã đổi IP`, { description: result.message });
      return;
    case "failed":
      toast.error(`Proxy #${id} đổi IP chưa thành công`, { description: result.message });
      return;
    case "started":
      toast.success(`Proxy #${id} đang đổi IP`, {
        description: "Thường mất 10–60 giây, trạng thái đổi IP sẽ tự cập nhật.",
      });
      return;
    case "pending":
      toast.info(`Proxy #${id} sẽ đổi IP khi máy trả proxy`, {
        description: result.message,
        action: force?.("Đổi ngay (ngắt máy đang dùng)"),
        duration: 10_000,
      });
      return;
    case "skipped":
      toast.warning(`Proxy #${id} chưa đổi IP`, {
        description: result.message,
        action: skippedByCooldown(result) ? force?.("Vẫn đổi IP") : undefined,
        duration: 8000,
      });
      return;
  }
}

export async function forceRotate(id: number, refresh: () => void): Promise<void> {
  try {
    notifyRotate(id, await proxiesApi.rotate(id, { force: true, wait: false }));
  } catch (error) {
    toast.error(`Không đổi IP được proxy #${id}`, { description: errorMessage(error) });
  } finally {
    refresh();
  }
}
