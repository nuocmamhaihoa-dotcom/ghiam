"use strict";

const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");
const { URL } = require("url");

const linkPath = process.env.PC_LINK || path.join(__dirname, "pc-link.json");
let link = {};
try {
  link = JSON.parse(fs.readFileSync(linkPath, "utf8"));
} catch {
  link = {};
}
const base = process.env.PC_BASE || link.base || "http://14.225.198.188:8088";
const token = process.env.PC_TOKEN || link.token || "";
const host = process.env.PC_HOST || link.host || require("os").hostname();
const slot = Number(process.env.PC_SLOT || link.slot || "1");
const doors = 16;
const getMs = 4000;
const counts = new Map();
let burned = false;

function request(url, options, body) {
  const u = new URL(url);
  const lib = u.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const req = lib.request(url, { method: options.method || "GET", headers: options.headers || {}, timeout: options.timeout || getMs }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode || 0, body: Buffer.concat(chunks).toString("utf8").slice(0, 900000) }));
    });
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy(new Error("timeout"));
    });
    if (body) req.write(body);
    req.end();
  });
}

async function api(path, payload) {
  const body = payload ? JSON.stringify(payload) : "";
  const res = await request(base + path, {
    method: payload ? "POST" : "GET",
    headers: payload ? { "content-type": "application/json", "content-length": Buffer.byteLength(body) } : {},
  }, body);
  return JSON.parse(res.body || "{}");
}

function parseCount(html) {
  const patterns = [
    /"comment_count"\s*:\s*\{\s*"total_count"\s*:\s*(\d+)/,
    /"comments"\s*:\s*\{\s*"total_count"\s*:\s*(\d+)/,
    /"total_comment_count"\s*:\s*(\d+)/,
    /"comment_count"\s*:\s*(\d+)/,
  ];
  for (const pattern of patterns) {
    const match = String(html).match(pattern);
    if (match) return Number(match[1]);
  }
  return null;
}

function parseComments(html) {
  const source = String(html || "");
  const legacy = [...source.matchAll(/"legacy_fbid"\s*:\s*"(\d+)"/g)].map((m) => m[1]);
  const texts = [...source.matchAll(/"text"\s*:\s*"((?:\\.|[^"\\]){1,400})"/g)];
  const comments = [];
  const n = Math.min(legacy.length, texts.length, 20);
  for (let i = 0; i < n; i += 1) {
    let text = texts[i][1];
    try { text = JSON.parse(`"${text}"`); } catch { /* keep raw */ }
    if (text && text.length >= 2) comments.push({ faceId: legacy[i], name: "", text, postId: "" });
  }
  return comments;
}

async function mapLimit(items, limit, worker) {
  const out = new Array(items.length);
  let cursor = 0;
  async function run() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      out[index] = await worker(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => run()));
  return out;
}

function facebookPost(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && ["www.facebook.com", "facebook.com", "m.facebook.com"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

async function scanPost(post) {
  if (!facebookPost(post.url)) return { post, kind: "counts_shell", count: null, comments: [] };
  let result;
  try {
    result = await request(post.url, { method: "GET" });
  } catch (err) {
    const kind = err.message === "timeout" ? "timeout" : "fetch_failed";
    return { post, kind, count: null, comments: [] };
  }
  if (result.status === 429) return { post, kind: "429", count: null, comments: [] };
  const count = parseCount(result.body);
  const comments = parseComments(result.body);
  const loginish = (result.body.match(/login|đăng nhập/gi) || []).length;
  if (!comments.length && loginish > 3) return { post, kind: "im", count, comments };
  return { post, kind: comments.length ? "ok" : "counts_shell", count, comments };
}

async function loop() {
  const command = await api(`/api/pc/command?token=${encodeURIComponent(token)}&host=${encodeURIComponent(host)}`);
  if (command.action === "renew" || burned) {
    await api("/api/pc/heartbeat", { token, host, homeBurned: true, note: "đứng GET" });
    return;
  }
  const slice = await api(`/api/pc/slice?token=${encodeURIComponent(token)}&host=${encodeURIComponent(host)}&slot=${slot}`);
  if (!slice.doors) {
    await api("/api/pc/heartbeat", { token, host, homeBurned: true, note: "0 GET" });
    return;
  }
  const rows = await mapLimit(slice.posts || [], doors, scanPost);
  if (rows.some((row) => row.kind === "im" || row.kind === "429")) {
    burned = true;
    const signals = rows.filter((row) => row.count != null).map((row) => ({ postId: row.post.id, count: row.count, hasText: false }));
    await api("/api/pc/heartbeat", { token, host, homeBurned: true, note: "im", signals });
    return;
  }
  const comments = [];
  const signals = [];
  for (const row of rows) {
    const previous = counts.get(row.post.id);
    const rose = row.count != null && (previous == null || row.count > previous);
    if (row.count != null) counts.set(row.post.id, row.count);
    if (!rose) continue;
    if (row.comments.length) {
      for (const comment of row.comments) comments.push({ ...comment, postId: row.post.id, postTitle: "" });
    } else {
      signals.push({ postId: row.post.id, count: row.count, hasText: false });
    }
  }
  await api("/api/pc/heartbeat", {
    token,
    host,
    homeBurned: false,
    cpu: 0,
    cores: doors,
    note: `slot ${slot} · ${slice.posts.length} bài`,
    comments,
    signals,
    seats: rows.slice(0, 12).map((row) => ({ post: row.post.id, viec: row.kind, ms: getMs, issue: row.kind === "ok" ? "không" : row.kind })),
  });
}

function fileHash(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").slice(0, 16);
}

async function download(url) {
  const res = await request(url, { method: "GET", timeout: 20000 });
  if (res.status !== 200) throw new Error("tải bản mới lỗi");
  return res.body;
}

async function maybeUpgrade() {
  const remote = await api("/api/pc/version");
  if (!remote.scanner || remote.scanner === fileHash(__filename)) return false;
  const body = await download(`${base}/pc/huong-a.js`);
  const next = path.join(__dirname, "huong-a.js.download");
  fs.writeFileSync(next, body);
  console.log("Có bản quét mới. Vòng hiện tại đã xong, chuyển bản mới.");
  if (process.env.PC_SUPERVISED === "1") return true;
  fs.copyFileSync(next, path.join(__dirname, "huong-a.js"));
  const child = spawn(process.execPath, [path.join(__dirname, "huong-a.js")], {
    cwd: __dirname,
    stdio: "inherit",
    env: process.env,
    detached: true,
  });
  child.unref();
  return true;
}

async function main() {
  if (path.basename(__filename) === "huong-a.next.js") {
    fs.copyFileSync(__filename, path.join(__dirname, "huong-a.js"));
  }
  if (!token) {
    console.error("Thiếu token trong pc-link.json");
    process.exit(1);
  }
  console.log(`Hướng A slot ${slot} trên ${host}`);
  for (;;) {
    const started = Date.now();
    try {
      await loop();
    } catch (err) {
      console.error(err.message);
    }
    if (process.env.PC_SUPERVISED !== "1") {
      try {
        if (await maybeUpgrade()) process.exit(75);
      } catch (err) {
        console.error(err.message);
      }
    }
    const wait = Math.max(0, 4000 - (Date.now() - started));
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
}

main();
