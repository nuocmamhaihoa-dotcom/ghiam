import { describe, expect, it } from "vitest";

import {
  cooldownLeft,
  describeRotation,
  formatDuration,
  formatLatency,
  formatRelative,
  formatRemaining,
  leaseAvailability,
} from "./format";

const NOW = Date.parse("2026-09-27T10:00:00Z");

function iso(offsetSec: number): string {
  return new Date(NOW + offsetSec * 1000).toISOString();
}

describe("formatDuration", () => {
  it.each([
    [0, "0 giây"],
    [-5, "0 giây"],
    [45, "45 giây"],
    [59.6, "1 phút"],
    [90, "1 phút 30 giây"],
    [3600, "1 giờ"],
    [3605, "1 giờ"],
    [3660, "1 giờ 1 phút"],
    [86_400, "1 ngày"],
    [90_061, "1 ngày 1 giờ"],
  ])("%s giây → %s", (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text);
  });
});

describe("formatLatency", () => {
  it("dùng ms dưới 1 giây, giây có 1 chữ số thập phân từ 1 giây", () => {
    expect(formatLatency(null)).toBe("—");
    expect(formatLatency(250)).toBe("250 ms");
    expect(formatLatency(1500)).toBe("1,5 s");
  });
});

describe("formatRelative", () => {
  it.each([
    [null, "—"],
    [iso(-5), "vừa xong"],
    [iso(-30), "30 giây trước"],
    [iso(-125), "2 phút trước"],
    [iso(7200), "sau 2 giờ"],
    [iso(-3 * 86_400), "3 ngày trước"],
  ])("%s → %s", (value, text) => {
    expect(formatRelative(value, NOW)).toBe(text);
  });
});

describe("formatRemaining", () => {
  it("chỉ trả về thời gian còn lại khi mốc ở tương lai", () => {
    expect(formatRemaining(null, NOW)).toBeNull();
    expect(formatRemaining(iso(-1), NOW)).toBeNull();
    expect(formatRemaining(iso(90), NOW)).toBe("1 phút 30 giây");
    expect(formatRemaining(iso(0.2), NOW)).toBe("1 giây");
  });
});

describe("cooldownLeft", () => {
  const proxy = { cooldown_remaining_sec: 50, rotation_cooldown_sec: 60, last_rotation_attempt_at: iso(-10) };

  it("đếm lùi theo đồng hồ trình duyệt", () => {
    expect(cooldownLeft(proxy, NOW)).toBe(50);
    expect(cooldownLeft(proxy, NOW + 30_000)).toBe(20);
    expect(cooldownLeft(proxy, NOW + 120_000)).toBe(0);
  });

  it("không bao giờ dài hơn con số máy chủ trả về dù đồng hồ máy bị lệch", () => {
    expect(cooldownLeft({ ...proxy, last_rotation_attempt_at: iso(100) }, NOW)).toBe(50);
  });

  it("máy chủ báo hết thời gian chờ hoặc chưa đổi IP lần nào thì không phải chờ", () => {
    expect(cooldownLeft({ ...proxy, cooldown_remaining_sec: 0 }, NOW)).toBe(0);
    expect(cooldownLeft({ ...proxy, last_rotation_attempt_at: null }, NOW)).toBe(0);
  });
});

describe("leaseAvailability", () => {
  const ready = {
    enabled: true,
    health: "alive",
    rotation_state: "idle",
    quarantined_until: null,
    active_leases: 1,
    max_concurrency: 3,
  } as const;

  it("proxy sống, rảnh, còn chỗ thì cho thuê được", () => {
    expect(leaseAvailability(ready, NOW)).toEqual({ available: true, text: "Sẵn sàng, còn 2 chỗ trống" });
  });

  it.each([
    [{ enabled: false, health: "dead" }, "Không, proxy đang tắt"],
    [{ health: "dead" }, "Không, proxy đang chết"],
    [{ health: "unchecked" }, "Chưa, proxy chưa được kiểm tra sống/chết"],
    [{ rotation_state: "rotating" }, "Tạm dừng trong lúc đổi IP"],
    [{ rotation_state: "pending" }, "Tạm dừng, chờ máy đang dùng trả proxy để đổi IP"],
    [{ quarantined_until: iso(300) }, "Tạm dừng, đang bị cách ly thêm 5 phút"],
    [{ active_leases: 3 }, "Đã đủ 3 máy dùng cùng lúc"],
  ] as const)("%o → %s", (change, text) => {
    expect(leaseAvailability({ ...ready, ...change }, NOW)).toEqual({ available: false, text });
  });

  it("hết thời gian cách ly thì cho thuê lại", () => {
    expect(leaseAvailability({ ...ready, quarantined_until: iso(-1) }, NOW).available).toBe(true);
  });
});

describe("describeRotation", () => {
  it.each([
    [{ kind: "static", rotation_mode: "none", rotation_interval_sec: 0 }, "IP cố định"],
    [{ kind: "rotating", rotation_mode: "none", rotation_interval_sec: 0 }, "Chưa có link đổi IP hoặc {session}"],
    [{ kind: "rotating", rotation_mode: "provider", rotation_interval_sec: 600 }, "Nhà cung cấp tự đổi mỗi 10 phút"],
    [{ kind: "rotating", rotation_mode: "url", rotation_interval_sec: 0 }, "Gọi link đổi IP"],
    [{ kind: "rotating", rotation_mode: "session", rotation_interval_sec: 3600 }, "Đổi session · tự đổi mỗi 1 giờ"],
  ] as const)("%o → %s", (proxy, text) => {
    expect(describeRotation(proxy)).toBe(text);
  });
});
