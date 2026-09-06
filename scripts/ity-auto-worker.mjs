#!/usr/bin/env node
/**
 * PM2 worker: continuously tick CallCraft ITY auto-pipeline.
 * Calls the Next.js app on localhost so ChốtKiểm credentials / libs stay in-app.
 *
 * Env:
 *   CALLCRAFT_BASE_URL     default http://127.0.0.1:3000
 *   ITY_AUTO_INTERVAL_SEC  fallback interval if status fetch fails (default 45)
 */

const BASE = (process.env.CALLCRAFT_BASE_URL || "http://127.0.0.1:3000").replace(
  /\/$/,
  "",
);
const FALLBACK_INTERVAL_SEC = Math.max(
  20,
  Number(process.env.ITY_AUTO_INTERVAL_SEC || 45) || 45,
);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function postAuto(action, extra = {}) {
  const res = await fetch(`${BASE}/api/ity/auto`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...extra }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

async function ensureEnabled() {
  const status = await postAuto("status");
  const enabled = Boolean(status.state?.enabled);
  if (!enabled) {
    console.log("[ity-auto] enabling pipeline…");
    await postAuto("enable");
  }
  return (
    Number(status.state?.config?.intervalSec) || FALLBACK_INTERVAL_SEC
  );
}

async function main() {
  console.log(`[ity-auto] worker started → ${BASE}`);
  let intervalSec = FALLBACK_INTERVAL_SEC;

  for (let i = 0; i < 30; i += 1) {
    try {
      intervalSec = await ensureEnabled();
      break;
    } catch (error) {
      console.warn(
        `[ity-auto] waiting for app… ${error instanceof Error ? error.message : error}`,
      );
      await sleep(5_000);
    }
  }

  while (true) {
    const started = Date.now();
    try {
      const data = await postAuto("tick");
      const result = data.result || {};
      intervalSec =
        Number(data.state?.config?.intervalSec) ||
        intervalSec ||
        FALLBACK_INTERVAL_SEC;
      console.log(
        `[ity-auto] ${result.message || data.state?.lastSummary || "tick ok"} · next in ${intervalSec}s`,
      );
      if (result.error || data.state?.lastError) {
        console.warn(
          `[ity-auto] warn: ${result.error || data.state?.lastError}`,
        );
      }
    } catch (error) {
      console.error(
        `[ity-auto] tick failed: ${error instanceof Error ? error.message : error}`,
      );
    }

    const elapsed = Date.now() - started;
    const waitMs = Math.max(5_000, intervalSec * 1000 - elapsed);
    await sleep(waitMs);
  }
}

main().catch((error) => {
  console.error("[ity-auto] fatal", error);
  process.exit(1);
});
