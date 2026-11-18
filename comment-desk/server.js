"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { URL } = require("url");
const lib = require("./scanlib");

const PORT = Number(process.env.PORT || 8088);
const DATA = process.env.DATA_DIR || path.join(__dirname, "data");
const PUBLIC = path.join(__dirname, "public");
const STATE_FILE = path.join(DATA, "state.json");

fs.mkdirSync(DATA, { recursive: true });

function vnStamp(date = new Date()) {
  const d = new Date(date.getTime() + 7 * 3600 * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

function vnDay(date = new Date()) {
  return vnStamp(date).slice(0, 10);
}

function loadState() {
  const blank = {
    posts: [],
    comments: [],
    proxies: [],
    staticProxies: [],
    pcs: [],
    log: [],
    hotIds: [],
    counts: {},
    misses: [],
    priority: [],
    rescue: [],
    renewals: {},
    sessions: {},
    pcToken: crypto.randomBytes(18).toString("hex"),
    passHash: "",
  };
  try {
    const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    return { ...blank, ...raw };
  } catch {
    return blank;
  }
}

let state = loadState();
state.renewals ||= {};
state.rescue ||= [];
let saveTimer = null;

function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    fs.writeFileSync(
      STATE_FILE,
      JSON.stringify({
        posts: state.posts,
        comments: state.comments.slice(0, 20000),
        proxies: state.proxies,
        staticProxies: state.staticProxies,
        pcs: state.pcs,
        log: state.log.slice(0, 5000),
        hotIds: state.hotIds,
        counts: state.counts,
        misses: state.misses.slice(0, 200),
        priority: state.priority.slice(0, 8),
        rescue: (state.rescue || []).slice(0, 32),
        renewals: state.renewals,
        sessions: state.sessions,
        pcToken: state.pcToken,
        passHash: state.passHash,
      }),
    );
  }, 200);
}

function hashPass(password, salt = crypto.randomBytes(16).toString("hex")) {
  const hash = crypto.scryptSync(password, salt, 32).toString("hex");
  return `${salt}:${hash}`;
}

function checkPass(password, stored) {
  const [salt, hash] = String(stored || "").split(":");
  if (!salt || !hash) return false;
  const got = crypto.scryptSync(password, salt, 32);
  const exp = Buffer.from(hash, "hex");
  return exp.length === got.length && crypto.timingSafeEqual(exp, got);
}

if (!state.passHash && process.env.ADMIN_PASS) {
  state.passHash = hashPass(process.env.ADMIN_PASS);
  saveSoon();
}
const sampleMode = !process.env.ADMIN_PASS && !state.passHash;

const sessions = new Map(Object.entries(state.sessions || {}));

function cookie(req) {
  const m = String(req.headers.cookie || "").match(/(?:^|;\s*)sid=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function authed(req) {
  const row = sessions.get(cookie(req));
  return !!(row && row.exp > Date.now());
}

function send(res, code, body, headers = {}) {
  const buf = Buffer.isBuffer(body) ? body : Buffer.from(body);
  res.writeHead(code, { "content-length": buf.length, ...headers });
  res.end(buf);
}

function json(res, code, obj, extra = {}) {
  send(res, code, JSON.stringify(obj), { "content-type": "application/json; charset=utf-8", ...extra });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let n = 0;
    req.on("data", (c) => {
      n += c.length;
      if (n > 3_000_000) {
        reject(new Error("body quá lớn"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function parseProxyLine(line) {
  const raw = line.trim();
  if (!raw || raw.startsWith("#")) return null;
  const [proxyPart, rotateUrl = ""] = raw.split("|");
  const bits = proxyPart.split(":");
  if (bits.length < 2) return null;
  const [host, port, user = "", pass = ""] = bits;
  if (!host || !Number(port)) return null;
  return {
    id: crypto.createHash("sha1").update(proxyPart).digest("hex").slice(0, 12),
    host,
    port: Number(port),
    user,
    pass,
    rotateUrl: rotateUrl.trim(),
    intervalMs: 300000,
    lastRotate: 0,
    status: "chưa dò",
    lastError: "",
    failTimes: [],
    skipUntil: 0,
  };
}

function freshPcs(now = Date.now()) {
  return state.pcs.filter((p) => now - p.seenAt < 20000);
}

function todayComments() {
  const day = vnDay();
  return state.comments.filter((c) => String(c.scannedAt || "").startsWith(day));
}

function todayLog(lane) {
  const day = vnDay();
  return state.log.filter((row) => String(row.at || "").startsWith(day) && (!lane || row.lane === lane));
}

function pushLog(row) {
  state.log.unshift({ ...row, at: vnStamp() });
  state.log = state.log.slice(0, 5000);
  saveSoon();
}

function storeComments(post, found, lane) {
  let added = 0;
  let old = 0;
  const seen = new Set(state.comments.map((c) => `${c.faceId}|${c.text}`));
  for (const c of found) {
    const key = `${c.faceId}|${c.text}`;
    if (seen.has(key)) {
      old += 1;
      continue;
    }
    seen.add(key);
    state.comments.unshift({
      id: crypto.randomBytes(6).toString("hex"),
      faceId: c.faceId,
      name: c.name,
      postId: post.id,
      postTitle: post.title || post.url,
      text: c.text,
      fbTime: c.fbTime || "",
      scannedAt: vnStamp(),
      lane,
    });
    added += 1;
  }
  if (added || old) saveSoon();
  return { added, old };
}

function rotateProxy(proxy) {
  if (!proxy.rotateUrl) return;
  proxy.lastRotate = Date.now();
}

function markProxy(proxy, kind) {
  const now = Date.now();
  if (kind === "timeout") {
    proxy.status = "timeout";
    proxy.lastError = "timeout";
    return;
  }
  if (kind === "fetch_failed" || kind === "proxy_connect") {
    lib.noteFail(proxy, now);
    saveSoon();
    return;
  }
  if (kind === "429" || kind === "im" || kind === "counts_shell") {
    proxy.status = kind;
    proxy.lastError = kind;
    proxy.skipUntil = now + 20000;
    rotateProxy(proxy);
    lib.releasePark(state.proxies, now);
    saveSoon();
    return;
  }
  proxy.status = "Live";
  proxy.lastError = "";
  proxy.failTimes = [];
  saveSoon();
}

const scan = { inflight: 0, seats: [], slots: {} };

function recordMiss(post, kind) {
  state.misses.unshift({ at: vnStamp(), postId: post.id, url: post.url, kind });
  state.misses = state.misses.slice(0, 200);
  pushLog({ lane: "radar", kind, postId: post.id });
}

const RENEW_GAP_MS = 10 * 60 * 1000;

function requestRenew(host) {
  const now = Date.now();
  const prev = state.renewals[host];
  if (prev && now - prev.requestedAt < RENEW_GAP_MS && prev.status !== "IP đã đổi") return;
  state.renewals[host] = {
    requestedAt: now,
    status: "chờ PC khởi động modem",
    oldIp: prev && prev.oldIp ? prev.oldIp : "",
    newIp: "",
  };
  saveSoon();
}

function findPost(postId) {
  return lib.leanPosts(state.posts).find((item) => item.id === postId || item.url.includes(postId));
}

function queueRescue(postId) {
  const post = findPost(postId);
  if (!post) return;
  if (state.rescue.some((item) => item.postId === post.id)) return;
  state.rescue.unshift({ postId: post.id, at: Date.now() });
  state.rescue = state.rescue.slice(0, 32);
  saveSoon();
}

function rememberSeat(row) {
  scan.seats.unshift(row);
  scan.seats = scan.seats.slice(0, 60);
}

function readRescue(post, proxy) {
  rememberSeat({
    lane: "leftover",
    progress: proxy.id,
    post: post.id,
    viec: "chờ PC nhà gửi chữ",
    ms: 0,
    issue: "không",
  });
}

function pump() {
  const now = Date.now();
  for (const proxy of state.proxies) {
    if (proxy.rotateUrl && now - (proxy.lastRotate || 0) >= (proxy.intervalMs || 300000)) rotateProxy(proxy);
  }
  lib.releasePark(state.proxies, now);
  const live = lib.liveProxies(state.proxies, now);
  const readers = live.slice(0, lib.LEFTOVER_INFLIGHT);
  scan.reserve = Math.max(0, live.length - readers.length);
  scan.hot = state.rescue.slice(0, 8).map((item) => ({ post: item.postId, priority: true, count: state.counts[item.postId] ?? null }));
  while (state.rescue.length && readers.length) {
    const job = state.rescue[state.rescue.length - 1];
    const post = findPost(job.postId);
    state.rescue.pop();
    if (!post) continue;
    readRescue(post, readers[0]);
  }
}

setInterval(pump, 400);

function countKind(rows, kind) {
  return rows.filter((row) => row.kind === kind).length;
}

function homePayload() {
  const now = Date.now();
  const pcs = freshPcs(now);
  const burned = pcs.length > 0 && pcs.every((p) => p.homeBurned);
  const windowN = lib.windowPosts(state.posts).length;
  const liveN = lib.liveProxies(state.proxies, now).length;
  const readers = Math.min(lib.LEFTOVER_INFLIGHT, liveN);
  const doors = Math.min(readers, state.rescue.length);
  const home = lib.homeDoors(pcs, now);
  const today = todayComments();
  const leftLog = todayLog("leftover");
  const aLog = todayLog("A");
  const leanN = lib.leanPosts(state.posts).length;
  const lanes = [
    {
      name: "A · PC www guest",
      doing: !pcs.length
        ? `500 vòng sẵn khi PC nối · trần ${lib.HOME_DOORS} GET/IP nhà · VPS không GET thay IP nhà`
        : burned
          ? "IP nhà im · 500 vòng sẵn · 0 GET thân"
          : `${pcs.length} PC đang giữ vòng · ${home} GET nhà`,
      today: countKind(aLog, "mới"),
      issue: burned ? "im" : "không",
    },
    {
      name: "D Hybrid",
      doing: `${Math.min(30, leanN)} ghế theo dõi · mỗi bài một claim · chữ lấy từ lần đọc www`,
      today: countKind(leftLog, "mới"),
      issue: "không",
    },
    {
      name: "G Radar",
      doing: `${windowN}/128 bài trong cửa sổ 10 giây · 4 PC mỗi máy 32 bài · 16 GET nhà`,
      today: todayLog("radar").length,
      issue: "không",
    },
    {
      name: "E · 4G chậm",
      doing: "đã đo quét nhiều mà 0 comment mới · hướng này tắt",
      today: 0,
      issue: "tắt",
    },
    {
      name: "F",
      doing: "không chạy · không phải đường đọc chữ",
      today: 0,
      issue: "tắt",
    },
    {
      name: "Leftover 4G-only www",
      doing: readers
        ? `${readers} line đọc bài vừa tăng số · ${Math.max(0, liveN - readers)} line dự phòng · hàng ${state.rescue.length}`
        : "0 GET · chưa có line 4G sống",
      today: today.filter((c) => c.lane === "leftover").length,
      issue: "không",
    },
  ];
  const mapSeat = (s) => ({ ...s, ok: String(s.viec).includes("mới") || String(s.viec).includes("cũ") || String(s.viec).includes("www") || String(s.viec).includes("nghe") });
  const aSeats = pcs.flatMap((p) => (p.seats || []).map((s) => mapSeat({
    progress: s.progress || p.host,
    post: s.post || "",
    viec: s.viec || (p.homeBurned ? "đứng GET" : "GET nhà"),
    ms: s.ms || 0,
    issue: s.issue || "không",
  }))).slice(0, 20);
  const gSeats = (scan.hot || []).map((row) => mapSeat({
    progress: row.priority ? "ưu tiên" : "đếm",
    post: row.post,
    viec: row.count == null ? "chưa có số" : `số comment ${row.count}`,
    ms: 0,
    issue: "không",
  }));
  const dSeats = scan.seats.filter((s) => s.lane === "leftover").slice(0, 16).map((s) => mapSeat({
    progress: `D ${s.post}`,
    post: s.post,
    viec: s.viec,
    ms: s.ms,
    issue: s.issue,
  }));
  return {
    user: process.env.ADMIN_USER || "monkey01",
    posts: state.posts.length,
    scanning: leanN,
    today: today.length,
    proxies: state.proxies.length,
    liveProxies: lib.liveProxies(state.proxies, now).length,
    staticProxies: state.staticProxies.length,
    doors,
    inflight: scan.inflight,
    homeDoors: home,
    lanes,
    seats: {
      A: aSeats,
      D: dSeats,
      G: gSeats,
      leftover: scan.seats.filter((s) => s.lane === "leftover").slice(0, 16).map(mapSeat),
    },
    facts: {
      mới: countKind(leftLog, "mới"),
      cũ: countKind(leftLog, "cũ"),
      im: countKind(leftLog, "im"),
      timeout: countKind(leftLog, "timeout"),
      "429": countKind(leftLog, "429"),
      counts_shell: countKind(leftLog, "counts_shell"),
      fetch_failed: countKind(leftLog, "fetch_failed"),
    },
    pcs: pcs.map((p) => ({
      host: p.host,
      cpu: p.cpu,
      ram: p.ram,
      cores: p.cores,
      homeBurned: !!p.homeBurned,
      note: p.note || "không",
      renew: state.renewals[p.host] || null,
    })),
    comments: today.slice(0, 80),
    misses: state.misses.slice(0, 30),
    hot: lib.windowPosts(state.posts).map((post) => ({ id: post.id, url: post.url, count: state.counts[post.id] ?? null })),
    rescue: state.rescue.length,
    banner: pcs.length
      ? `Đã nối ${pcs.length} PC. Bản web này không tự gọi Facebook. Danh sách ${state.posts.length} bài.`
      : `Chưa có PC nhà. Bản web này không tự gọi Facebook. Danh sách ${state.posts.length} bài.`,
  };
}

function requireUser(req, res) {
  if (authed(req)) return true;
  json(res, 401, { error: "chưa đăng nhập" });
  return false;
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://localhost");
  try {
    if (req.method === "GET" && u.pathname === "/health") {
      json(res, 200, { ok: true, app: "tech666-analyzer", mode: "huong-a-d-g-leftover" });
      return;
    }
    if (req.method === "GET" && (u.pathname === "/" || u.pathname === "/app.js")) {
      const file = u.pathname === "/app.js" ? "app.js" : "index.html";
      send(res, 200, fs.readFileSync(path.join(PUBLIC, file)), {
        "content-type": file.endsWith(".js") ? "text/javascript; charset=utf-8" : "text/html; charset=utf-8",
      });
      return;
    }
    if (req.method === "POST" && u.pathname === "/api/login") {
      const raw = await readBody(req);
      const form = String(req.headers["content-type"] || "").includes("application/x-www-form-urlencoded");
      let username = "";
      let password = "";
      if (form) {
        const params = new URLSearchParams(raw);
        username = params.get("username") || params.get("user") || "";
        password = params.get("password") || params.get("pass") || "";
      } else {
        const body = JSON.parse(raw || "{}");
        username = body.user || body.username || "";
        password = body.pass || body.password || "";
      }
      const user = process.env.ADMIN_USER || "monkey01";
      if (username !== user || !checkPass(String(password), state.passHash)) {
        if (form) {
          send(res, 303, "", { location: "/?loi=1" });
          return;
        }
        json(res, 401, { error: "sai tài khoản" });
        return;
      }
      const sid = crypto.randomBytes(24).toString("hex");
      sessions.set(sid, { exp: Date.now() + 7 * 86400000 });
      state.sessions[sid] = sessions.get(sid);
      saveSoon();
      const secure = req.headers["x-forwarded-proto"] === "https" ? "; Secure" : "";
      const cookie = `sid=${sid}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax${secure}`;
      if (form) {
        send(res, 303, "", { location: "/", "set-cookie": cookie });
        return;
      }
      json(res, 200, { ok: true }, { "set-cookie": cookie });
      return;
    }
    if (req.method === "POST" && u.pathname === "/api/logout") {
      const sid = cookie(req);
      sessions.delete(sid);
      delete state.sessions[sid];
      saveSoon();
      json(res, 200, { ok: true });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/me") {
      if (sampleMode && !authed(req)) {
        json(res, 200, { ok: true, sample: true, user: "xem thử" });
        return;
      }
      json(res, 200, { ok: authed(req), user: process.env.ADMIN_USER || "monkey01" });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/home") {
      if (sampleMode && !authed(req)) {
        json(res, 200, {
          sample: true,
          user: "xem thử",
          posts: 128,
          scanning: 128,
          today: 2,
          proxies: 10,
          liveProxies: 10,
          staticProxies: 0,
          doors: 1,
          inflight: 0,
          homeDoors: 48,
          lanes: [
            { name: "A · PC www guest", doing: "4 PC · mỗi máy 32 bài · 16 GET", today: 2, issue: "không" },
            { name: "G Radar", doing: "128/128 bài trong cửa sổ 10 giây", today: 0, issue: "không" },
            { name: "Leftover 4G-only www", doing: "8 line đọc bài vừa tăng số · 2 line dự phòng", today: 1, issue: "không" },
          ],
          seats: { A: [], D: [], G: [], leftover: [] },
          facts: { mới: 1, cũ: 0, im: 0, timeout: 0, "429": 0, counts_shell: 0, fetch_failed: 0 },
          pcs: [{ host: "PC-1", cpu: 12, ram: "16 GB", cores: 8, homeBurned: false, note: "slot 1" }],
          comments: [{ fbTime: "", scannedAt: "2026-11-18 09:00:00", faceId: "100", name: "Khách mẫu", postId: "bai-1", postTitle: "Bài mẫu", text: "Bình luận mẫu, không phải dữ liệu thật" }],
          misses: [],
          hot: [],
          rescue: 1,
          banner: "Dữ liệu mẫu để xem giao diện. Chưa kết nối Facebook.",
        });
        return;
      }
      if (!requireUser(req, res)) return;
      json(res, 200, homePayload());
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/posts") {
      if (!requireUser(req, res)) return;
      json(res, 200, { posts: state.posts, hotIds: state.hotIds });
      return;
    }
    if (req.method === "POST" && u.pathname === "/api/hot") {
      if (!requireUser(req, res)) return;
      const lines = String(JSON.parse((await readBody(req)) || "{}").text || "").split(/\r?\n/);
      const ids = [];
      for (const line0 of lines) {
        const line = line0.trim();
        if (!line || ids.length >= 8) continue;
        const post = state.posts.find((item) => item.url === line || item.id === line || item.url.includes(line));
        if (post && !ids.includes(post.id)) ids.push(post.id);
      }
      state.hotIds = ids;
      saveSoon();
      json(res, 200, { ok: true, count: ids.length });
      return;
    }
    if (req.method === "POST" && u.pathname === "/api/posts") {
      if (!requireUser(req, res)) return;
      const lines = String(JSON.parse((await readBody(req)) || "{}").text || "").split(/\r?\n/);
      const posts = [];
      for (const line0 of lines) {
        const line = line0.trim();
        if (!line || line.startsWith("#") || posts.length >= 2000) continue;
        let url = line;
        if (/^\d+$/.test(line)) url = `https://www.facebook.com/${line}`;
        if (!/^https?:\/\//i.test(url)) url = `https://www.facebook.com/${line.replace(/^\/+/, "")}`;
        let parsed;
        try { parsed = new URL(url); } catch { continue; }
        if (parsed.protocol !== "https:" || !["www.facebook.com", "facebook.com", "m.facebook.com"].includes(parsed.hostname)) continue;
        posts.push({ id: crypto.createHash("sha1").update(url).digest("hex").slice(0, 10), url, title: "" });
      }
      state.posts = posts;
      saveSoon();
      json(res, 200, { ok: true, count: posts.length, lean: lib.leanPosts(posts).length });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/proxies") {
      if (!requireUser(req, res)) return;
      json(res, 200, {
        proxies: state.proxies.map((p) => ({ id: p.id, host: p.host, port: p.port, status: p.status, lastError: p.lastError, rotate: p.rotateUrl ? "có" : "không" })),
        staticCount: state.staticProxies.length,
      });
      return;
    }
    if (req.method === "POST" && u.pathname === "/api/proxies") {
      if (!requireUser(req, res)) return;
      const body = JSON.parse((await readBody(req)) || "{}");
      if (body.kind === "static") {
        state.staticProxies = String(body.text || "").split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith("#"));
        saveSoon();
        json(res, 200, { ok: true, count: state.staticProxies.length, usedForGet: false });
        return;
      }
      state.proxies = String(body.text || "").split(/\r?\n/).map(parseProxyLine).filter(Boolean);
      saveSoon();
      json(res, 200, { ok: true, count: state.proxies.length });
      return;
    }
    if (req.method === "GET" && u.pathname === "/pc/renew-home-ip.ps1") {
      send(res, 200, fs.readFileSync(path.join(PUBLIC, "renew-home-ip.ps1")), {
        "content-type": "text/plain; charset=utf-8",
      });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/pc/command") {
      if (u.searchParams.get("token") !== state.pcToken) {
        json(res, 401, { error: "sai token PC" });
        return;
      }
      const host = String(u.searchParams.get("host") || "");
      const renew = state.renewals[host];
      const pending = renew && renew.status === "chờ PC khởi động modem";
      json(res, 200, { action: pending ? "renew" : "scan", doors: pending ? 0 : lib.HOME_DOORS });
      return;
    }
    if (req.method === "POST" && u.pathname === "/api/pc/renew") {
      const body = JSON.parse((await readBody(req)) || "{}");
      if (body.token !== state.pcToken) {
        json(res, 401, { error: "sai token PC" });
        return;
      }
      const host = String(body.host || "").slice(0, 80);
      const oldIp = String(body.oldIp || "");
      const newIp = String(body.newIp || "");
      const changed = Boolean(newIp) && newIp !== oldIp;
      let status = "IP không đổi, vẫn đứng GET";
      if (!body.started) status = "chưa có lệnh modem, IP giữ nguyên";
      if (changed) status = "IP đã đổi";
      state.renewals[host] = { requestedAt: Date.now(), status, oldIp, newIp };
      const pc = state.pcs.find((item) => item.host === host);
      if (pc && changed) pc.homeBurned = false;
      saveSoon();
      json(res, 200, { ok: true, changed, doors: changed ? lib.HOME_DOORS : 0 });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/pc/version") {
      const hash = (name) => crypto.createHash("sha256").update(fs.readFileSync(path.join(PUBLIC, name))).digest("hex").slice(0, 16);
      json(res, 200, { scanner: hash("huong-a.js"), runner: hash("pc-run.js") });
      return;
    }
    if (req.method === "GET" && u.pathname === "/pc/CaiDat-PC.exe") {
      send(res, 200, fs.readFileSync(path.join(PUBLIC, "CaiDat-PC.exe")), {
        "content-type": "application/octet-stream",
        "content-disposition": "attachment; filename=CaiDat-PC.exe",
      });
      return;
    }
    if (req.method === "GET" && u.pathname === "/pc/pc-run.js") {
      send(res, 200, fs.readFileSync(path.join(PUBLIC, "pc-run.js")), {
        "content-type": "text/javascript; charset=utf-8",
      });
      return;
    }
    if (req.method === "GET" && u.pathname === "/pc/pc-link.json") {
      send(res, 200, fs.readFileSync(path.join(PUBLIC, "pc-link.json")), {
        "content-type": "application/json; charset=utf-8",
      });
      return;
    }
    if (req.method === "GET" && u.pathname === "/pc/huong-a.js") {
      send(res, 200, fs.readFileSync(path.join(PUBLIC, "huong-a.js")), {
        "content-type": "text/javascript; charset=utf-8",
      });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/pc/slice") {
      if (u.searchParams.get("token") !== state.pcToken) {
        json(res, 401, { error: "sai token PC" });
        return;
      }
      const slot = Number(u.searchParams.get("slot") || 0);
      const host = String(u.searchParams.get("host") || "");
      const renew = state.renewals[host];
      const pending = renew && renew.status === "chờ PC khởi động modem";
      json(res, 200, {
        slot,
        posts: lib.pcSlice(state.posts, slot),
        doors: pending ? 0 : lib.HOME_DOORS,
        getMs: 4000,
      });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/pc/posts") {
      if (u.searchParams.get("token") !== state.pcToken) {
        json(res, 401, { error: "sai token PC" });
        return;
      }
      json(res, 200, { posts: lib.leanPosts(state.posts), doors: lib.HOME_DOORS, getMs: 5200 });
      return;
    }
    if (req.method === "POST" && u.pathname === "/api/pc/heartbeat") {
      const body = JSON.parse((await readBody(req)) || "{}");
      if (body.token !== state.pcToken) {
        json(res, 401, { error: "sai token PC" });
        return;
      }
      const host = String(body.host || "PC").slice(0, 80);
      state.pcs = [{
        host,
        cpu: Number(body.cpu || 0),
        ram: String(body.ram || ""),
        cores: Number(body.cores || 0),
        homeBurned: !!body.homeBurned,
        note: String(body.note || "không"),
        seats: Array.isArray(body.seats) ? body.seats.slice(0, 40) : [],
        seenAt: Date.now(),
      }, ...state.pcs.filter((p) => p.host !== host)].slice(0, 8);
      if (body.homeBurned) requestRenew(host);
      let added = 0;
      if (Array.isArray(body.signals)) {
        for (const signal of body.signals.slice(0, lib.HOME_DOORS)) {
          if (!signal || signal.hasText) continue;
          queueRescue(String(signal.postId || ""));
        }
      }
      if (Array.isArray(body.comments)) {
        for (const c of body.comments.slice(0, 100)) {
          if (!c || !c.text) continue;
          const saved = storeComments(
            { id: String(c.postId || host), url: "", title: String(c.postTitle || "") },
            [{ faceId: String(c.faceId || ""), name: String(c.name || ""), text: String(c.text) }],
            "A",
          );
          added += saved.added;
        }
        if (added) pushLog({ lane: "A", kind: "mới", postId: host, added });
      }
      saveSoon();
      json(res, 200, { ok: true, added, doors: body.homeBurned ? 0 : lib.HOME_DOORS });
      return;
    }
    if (req.method === "GET" && u.pathname === "/api/pc-token") {
      if (!requireUser(req, res)) return;
      json(res, 200, { token: state.pcToken });
      return;
    }
    json(res, 404, { error: "không có" });
  } catch (err) {
    json(res, 500, { error: "lỗi" });
  }
});

server.listen(PORT, "0.0.0.0", () => {
  process.stdout.write(`tech666-analyzer http://0.0.0.0:${PORT}\n`);
});
