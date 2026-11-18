"use strict";

const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");
const { URL } = require("url");

const dir = __dirname;
const linkPath = process.env.PC_LINK || path.join(dir, "pc-link.json");
const scannerPath = path.join(dir, "huong-a.js");

function readLink() {
  try {
    return JSON.parse(fs.readFileSync(linkPath, "utf8"));
  } catch {
    return {};
  }
}

function fileHash(file) {
  if (!fs.existsSync(file)) return "";
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").slice(0, 16);
}

function download(url) {
  const u = new URL(url);
  const lib = u.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const req = lib.get(url, { timeout: 20000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        download(new URL(res.headers.location, url).href).then(resolve, reject);
        return;
      }
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    });
    req.on("error", reject);
    req.on("timeout", () => req.destroy(new Error("timeout")));
  });
}

let child = null;

function startScanner() {
  if (child) return;
  const proc = spawn(process.execPath, [scannerPath], {
    cwd: dir,
    stdio: "inherit",
    env: { ...process.env, PC_SUPERVISED: "1" },
  });
  child = proc;
  proc.on("exit", () => {
    if (child !== proc) return;
    child = null;
    setTimeout(startScanner, 1000);
  });
}

function restartScanner() {
  const old = child;
  child = null;
  if (old) old.kill();
  startScanner();
}

async function tick(base) {
  const remote = JSON.parse((await download(new URL("/api/pc/version", base).href)).toString("utf8"));
  if (remote.runner && remote.runner !== fileHash(__filename)) {
    const next = path.join(dir, "pc-run.next.js");
    fs.writeFileSync(next, await download(new URL("/pc/pc-run.js", base).href));
    const old = child;
    child = null;
    if (old) old.kill();
    const nextProc = spawn(process.execPath, [next], { cwd: dir, stdio: "inherit", env: process.env, detached: true });
    nextProc.unref();
    process.exit(0);
  }
  if (remote.scanner && remote.scanner !== fileHash(scannerPath)) {
    const fresh = await download(new URL("/pc/huong-a.js", base).href);
    const tmp = path.join(dir, "huong-a.js.download");
    fs.writeFileSync(tmp, fresh);
    fs.copyFileSync(tmp, scannerPath);
    fs.unlinkSync(tmp);
    console.log("Đã cập nhật bản quét mới ngay");
    restartScanner();
  }
}

async function main() {
  if (path.basename(__filename) === "pc-run.next.js") {
    fs.copyFileSync(__filename, path.join(dir, "pc-run.js"));
  }
  const link = readLink();
  const base = process.env.PC_BASE || link.base || "http://14.225.198.188:8088";
  if (!fs.existsSync(scannerPath)) {
    fs.writeFileSync(scannerPath, await download(new URL("/pc/huong-a.js", base).href));
  }
  startScanner();
  for (;;) {
    try {
      await tick(base);
    } catch (err) {
      console.error(err.message);
    }
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
}

main();
