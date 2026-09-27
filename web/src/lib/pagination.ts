/**
 * Các nút trang cần hiện (luôn tối đa 7 ô để thanh phân trang không nhảy),
 * `null` là dấu "…". Ví dụ trang 50/100 → 1 … 49 50 51 … 100.
 */
export function pageWindow(page: number, pageCount: number): (number | null)[] {
  if (pageCount <= 7) {
    return Array.from({ length: Math.max(pageCount, 1) }, (_, index) => index + 1);
  }
  const current = Math.min(Math.max(page, 1), pageCount);
  let start = current - 1;
  let end = current + 1;
  if (current <= 4) {
    start = 2;
    end = 5;
  } else if (current >= pageCount - 3) {
    start = pageCount - 4;
    end = pageCount - 1;
  }
  const items: (number | null)[] = [1];
  if (start > 2) {
    items.push(null);
  }
  for (let item = start; item <= end; item += 1) {
    items.push(item);
  }
  if (end < pageCount - 1) {
    items.push(null);
  }
  items.push(pageCount);
  return items;
}

export function pageCountOf(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}
