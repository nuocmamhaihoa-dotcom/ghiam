/* Gửi nốt video khi trang iPhone đã đóng hoặc bị iPhone dừng. Máy chủ đọc tiếp sau khi nhận đủ. */
const CARRY_DB = "fb-poller-carry";
const CARRY_STORE = "videos";
const CARRY_KEEP_MS = 2 * 86400000;
const CHUNK = 4 * 1024 * 1024;

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(Promise.resolve(self.clients.claim()).then(() => carryWhenFree()));
});

self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "carry") return;
  event.waitUntil(carryWhenFree());
});

self.addEventListener("sync", (event) => {
  if (event.tag !== "video-carry") return;
  event.waitUntil(carryWhenFree());
});

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(CARRY_DB, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(CARRY_STORE)) {
        db.createObjectStore(CARRY_STORE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("db"));
  });
}

function withStore(mode, run) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(CARRY_STORE, mode);
    const store = tx.objectStore(CARRY_STORE);
    let result;
    let failed = false;
    try {
      result = run(store);
    } catch (error) {
      reject(error);
      return;
    }
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => {
      failed = true;
      reject(tx.error || new Error("tx"));
    };
    tx.onabort = () => {
      if (!failed) reject(tx.error || new Error("abort"));
    };
  }));
}

function readAll() {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(CARRY_STORE, "readonly");
    const request = tx.objectStore(CARRY_STORE).getAll();
    request.onsuccess = () => resolve(request.result || []);
    request.onerror = () => reject(request.error || new Error("read"));
  }));
}

function putRow(row) {
  return withStore("readwrite", (store) => {
    store.put(row);
  });
}

function deleteRow(key) {
  return withStore("readwrite", (store) => {
    store.delete(key);
  });
}

function authHeaders(row, extra) {
  const headers = Object.assign({}, extra || {});
  if (row && row.token) headers.Authorization = "Bearer " + row.token;
  return headers;
}

async function tell(message) {
  const pages = await self.clients.matchAll({ includeUncontrolled: true, type: "window" });
  pages.forEach((page) => {
    page.postMessage(message);
  });
}

function chunkHeld(status, offset, end) {
  const spans = status && status.spans ? status.spans : [];
  for (let index = 0; index < spans.length; index += 1) {
    const span = spans[index];
    if (span && span[0] <= offset && span[1] >= end) return true;
  }
  return !!(status && typeof status.offset === "number" && status.offset >= end);
}

async function readUpload(row, uploadId) {
  const response = await fetch("/v1/recordings/uploads/" + uploadId, { headers: authHeaders(row) });
  if (response.status === 404) return { missing: true };
  if (!response.ok) return {};
  return { status: await response.json() };
}

async function markJob(row, jobId, uploadId) {
  const next = {
    key: row.key,
    name: row.name || "video",
    size: row.size || 0,
    lastModified: row.lastModified || 0,
    type: row.type || "",
    uploadId: uploadId || row.uploadId || "",
    jobId: jobId,
    source: row.source || "",
    token: row.token || "",
    at: Date.now(),
    blob: null,
  };
  await putRow(next);
  await tell({
    type: "job",
    jobId: jobId,
    uploadId: next.uploadId,
    key: row.key,
    name: next.name,
  });
}

async function startUpload(row) {
  const response = await fetch("/v1/recordings/uploads", {
    method: "POST",
    headers: authHeaders(row, { "Content-Type": "application/json" }),
    body: JSON.stringify({
      name: row.name || "man-hinh.mp4",
      size: row.size,
      source: row.source || "",
    }),
  });
  if (!response.ok) return "";
  const started = await response.json();
  return started && started.uploadId ? String(started.uploadId) : "";
}

async function finishUpload(row, uploadId) {
  const response = await fetch("/v1/recordings/uploads/" + uploadId + "/finish", {
    method: "POST",
    headers: authHeaders(row),
  });
  if (!response.ok) return "";
  const data = await response.json();
  return data && data.jobId ? String(data.jobId) : "";
}

async function putChunk(row, uploadId, offset, bytes) {
  const response = await fetch("/v1/recordings/uploads/" + uploadId + "?offset=" + offset, {
    method: "PUT",
    headers: authHeaders(row, { "Content-Type": "application/octet-stream" }),
    body: bytes,
  });
  let data = {};
  try { data = await response.json(); } catch (error) { data = {}; }
  return { status: response.status, data: data };
}

function pause(misses) {
  return new Promise((resolve) => setTimeout(resolve, Math.min(8000, 500 * Math.max(1, misses))));
}

async function carryRow(row) {
  if (!row || !row.key) return;
  if (Date.now() - (Number(row.at) || 0) > CARRY_KEEP_MS) {
    await deleteRow(row.key);
    return;
  }
  if (row.jobId) {
    try {
      const response = await fetch("/v1/recordings/jobs/" + row.jobId, { headers: authHeaders(row) });
      if (response.ok) {
        const job = await response.json();
        if (job && job.done && !job.error) await deleteRow(row.key);
        return;
      }
      if (response.status !== 404) return;
      row.jobId = "";
    } catch (error) {
      return;
    }
  }
  if (!row.blob || !row.size) return;
  let uploadId = row.uploadId ? String(row.uploadId) : "";
  let misses = 0;
  while (Date.now() - (Number(row.at) || 0) <= CARRY_KEEP_MS) {
    if (!uploadId) {
      uploadId = await startUpload(row);
      if (!uploadId) {
        misses += 1;
        await pause(misses);
        continue;
      }
      row.uploadId = uploadId;
      await putRow(row);
    }
    const found = await readUpload(row, uploadId);
    if (found.missing) {
      uploadId = "";
      row.uploadId = "";
      misses += 1;
      await pause(misses);
      continue;
    }
    const status = found.status || {};
    if (status.finished && status.jobId) {
      await markJob(row, String(status.jobId), uploadId);
      return;
    }
    const size = Number(status.size) || Number(row.size) || 0;
    if (!size) {
      misses += 1;
      await pause(misses);
      continue;
    }
    let offset = -1;
    for (let cursor = 0; cursor < size; cursor += CHUNK) {
      const end = Math.min(size, cursor + CHUNK);
      if (!chunkHeld(status, cursor, end)) {
        offset = cursor;
        break;
      }
    }
    if (offset < 0) {
      const jobId = await finishUpload(row, uploadId);
      if (jobId) {
        await markJob(row, jobId, uploadId);
        return;
      }
      misses += 1;
      await pause(misses);
      continue;
    }
    const end = Math.min(size, offset + CHUNK);
    let bytes = null;
    for (let readTry = 0; readTry < 4 && !bytes; readTry += 1) {
      try {
        bytes = await row.blob.slice(offset, end).arrayBuffer();
      } catch (error) {
        await new Promise((resolve) => setTimeout(resolve, 300 * (readTry + 1)));
      }
    }
    if (!bytes) {
      misses += 1;
      await pause(misses);
      continue;
    }
    let landed = false;
    let lost = false;
    for (let attempt = 0; attempt < 8 && !landed && !lost; attempt += 1) {
      let reply;
      try {
        reply = await putChunk(row, uploadId, offset, bytes);
      } catch (error) {
        await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
        continue;
      }
      if (reply.status === 404) {
        lost = true;
        break;
      }
      if (reply.status === 401 || reply.status === 413 || reply.status === 507) {
        await pause(attempt + 1);
        continue;
      }
      const mark = typeof reply.data.end === "number" ? reply.data.end : reply.data.offset;
      landed = (reply.status >= 200 && reply.status < 300 && typeof mark === "number" && mark >= end)
        || (reply.status === 409 && typeof reply.data.offset === "number" && reply.data.offset >= end);
      if (!landed) await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
    }
    if (lost) {
      uploadId = "";
      row.uploadId = "";
      misses += 1;
      await pause(misses);
      continue;
    }
    if (!landed) {
      misses += 1;
      await pause(misses);
      continue;
    }
    misses = 0;
    await tell({
      type: "progress",
      key: row.key,
      loaded: end,
      total: size,
    });
  }
}

let carryTask = null;

function carryWhenFree() {
  if (carryTask) return carryTask;
  const locks = self.navigator && navigator.locks && navigator.locks.request;
  const run = typeof locks === "function"
    ? navigator.locks.request("fb-carry", () => carryBody())
    : carryBody();
  carryTask = Promise.resolve(run).finally(() => { carryTask = null; });
  return carryTask;
}

async function carryBody() {
  const rows = await readAll();
  for (let index = 0; index < rows.length; index += 1) {
    try {
      await carryRow(rows[index]);
    } catch (error) { /* khúc sau sẽ nối khi trang hoặc nền chạy lại */ }
  }
}

