"use strict";

const LEAN_CAP = 500;
const HOT_WINDOW = 128;
const POSTS_PER_PC = 32;
const PC_SLOTS = 4;
const LEFTOVER_INFLIGHT = 8;
const HOME_DOORS = 16;
const GET_4G_MS = 5200;
const MAX_PARK = 10;
const FAIL_WINDOW_MS = 60000;

function leanPosts(posts) {
  return (posts || []).slice(0, LEAN_CAP);
}

function windowPosts(posts) {
  return leanPosts(posts).slice(0, HOT_WINDOW);
}

function pcSlice(posts, slot) {
  const index = Number(slot) - 1;
  if (!Number.isInteger(index) || index < 0 || index >= PC_SLOTS) return [];
  const all = windowPosts(posts);
  return all.slice(index * POSTS_PER_PC, (index + 1) * POSTS_PER_PC);
}

function liveProxies(proxies, now) {
  return (proxies || []).filter((p) => p.status !== "Die" && (p.skipUntil || 0) <= now);
}

function leftoverDoors(proxies, posts, now) {
  const n = leanPosts(posts).length;
  const ready = liveProxies(proxies, now).length;
  if (n <= 0 || ready <= 0) return 0;
  return Math.min(LEFTOVER_INFLIGHT, ready, n);
}

function homeDoors(pcs, now) {
  const fresh = (pcs || []).filter((p) => now - (p.seenAt || 0) < 20000);
  if (!fresh.length) return 0;
  if (fresh.every((p) => p.homeBurned)) return 0;
  const open = fresh.filter((p) => !p.homeBurned).length;
  return Math.min(HOME_DOORS * open, HOME_DOORS);
}

function releasePark(proxies, now) {
  const parked = (proxies || []).filter((p) => p.status !== "Die" && (p.skipUntil || 0) > now);
  if (parked.length <= MAX_PARK) return parked.length;
  parked.sort((a, b) => a.skipUntil - b.skipUntil);
  for (const p of parked.slice(0, parked.length - MAX_PARK)) p.skipUntil = 0;
  return MAX_PARK;
}

function noteFail(proxy, now) {
  proxy.failTimes = (proxy.failTimes || []).filter((t) => now - t < FAIL_WINDOW_MS);
  proxy.failTimes.push(now);
  proxy.status = proxy.failTimes.length >= 2 ? "Die" : "fetch failed";
  proxy.lastError = proxy.status;
}

function decodeFb(text) {
  try {
    return JSON.parse(`"${text}"`);
  } catch {
    return String(text).replace(/\\n/g, "\n").replace(/\\"/g, '"');
  }
}

function parseCount(html) {
  const source = String(html || "");
  const patterns = [
    /"comment_count"\s*:\s*\{\s*"total_count"\s*:\s*(\d+)/,
    /"comments"\s*:\s*\{\s*"total_count"\s*:\s*(\d+)/,
    /"total_comment_count"\s*:\s*(\d+)/,
    /"comment_count"\s*:\s*(\d+)/,
  ];
  for (const pattern of patterns) {
    const match = source.match(pattern);
    if (match) return Number(match[1]);
  }
  return null;
}

function decideRadar(previousCount, nextCount, kind, hasText) {
  if (previousCount == null) {
    if (hasText) return { action: "save" };
    if (kind === "ok") return { action: "miss", kind: "no_legacy" };
    return { action: "miss", kind };
  }
  if (nextCount == null) {
    if (hasText) return { action: "save" };
    return { action: "miss", kind: kind === "ok" ? "no_legacy" : kind };
  }
  if (nextCount <= previousCount) return { action: "hold" };
  if (hasText) return { action: "save" };
  return { action: "miss", kind: kind === "ok" ? "no_legacy" : kind };
}

function hotPosts(posts, hotIds, priorityIds) {
  const byId = new Map((posts || []).map((post) => [post.id, post]));
  const ordered = [];
  const push = (id) => {
    const post = byId.get(id);
    if (!post || ordered.some((item) => item.id === post.id)) return;
    ordered.push(post);
  };
  for (const id of priorityIds || []) push(id);
  for (const id of hotIds || []) push(id);
  for (const post of posts || []) push(post.id);
  return ordered.slice(0, LEFTOVER_INFLIGHT);
}

function parseGuest(html, status) {
  if (status === 429) return { kind: "429", comments: [], title: "" };
  const source = String(html || "");
  const title = (source.match(/<title>([^<]{1,180})/i) || [])[1] || "";
  const legacy = [...source.matchAll(/"legacy_fbid"\s*:\s*"(\d+)"/g)].map((m) => m[1]);
  const texts = [...source.matchAll(/"text"\s*:\s*"((?:\\.|[^"\\]){1,400})"/g)].map((m) => decodeFb(m[1]).trim());
  const names = [...source.matchAll(/"name"\s*:\s*"((?:\\.|[^"\\]){1,80})"/g)].map((m) => decodeFb(m[1]).trim());
  const useful = texts.filter((t) => t.length >= 2 && !/^(like|reply|share|comment|thích|trả lời|xem thêm)$/i.test(t));
  if (!legacy.length) {
    const loginish = (source.match(/login|đăng nhập/gi) || []).length;
    return { kind: loginish > 3 ? "im" : "counts_shell", comments: [], title };
  }
  const comments = [];
  const n = Math.min(legacy.length, useful.length, 30);
  for (let i = 0; i < n; i += 1) {
    comments.push({ faceId: legacy[i], name: names[i] || "", text: useful[i] });
  }
  return { kind: comments.length ? "ok" : "counts_shell", comments, title };
}

function explainSeat(kind, saved) {
  if (kind === "ok" && saved && saved.added) return { viec: `mới ${saved.added}`, issue: "không" };
  if (kind === "ok") return { viec: `cũ ${saved ? saved.old : 0}`, issue: "không" };
  if (kind === "im") return { viec: "im · đứng GET", issue: "im" };
  if (kind === "counts_shell") return { viec: "HTML trống", issue: "counts_shell" };
  if (kind === "429") return { viec: "429", issue: "429" };
  if (kind === "timeout") return { viec: "timeout", issue: "timeout" };
  if (kind === "fetch_failed") return { viec: "fetch failed", issue: "fetch failed" };
  if (kind === "no_door") return { viec: "0 GET", issue: "không" };
  return { viec: kind, issue: kind };
}

module.exports = {
  LEAN_CAP,
  HOT_WINDOW,
  POSTS_PER_PC,
  PC_SLOTS,
  LEFTOVER_INFLIGHT,
  HOME_DOORS,
  GET_4G_MS,
  MAX_PARK,
  leanPosts,
  windowPosts,
  pcSlice,
  liveProxies,
  leftoverDoors,
  homeDoors,
  releasePark,
  noteFail,
  parseCount,
  decideRadar,
  hotPosts,
  parseGuest,
  explainSeat,
};
