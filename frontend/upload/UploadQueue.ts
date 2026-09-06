
/** Lightweight upload queue helper for 1000+ files. */
export type UploadItem = {
  id: string;
  filename: string;
  sizeBytes: number;
  status: "queued" | "uploading" | "done" | "failed" | "retrying";
  progress: number;
  etaSec: number;
  retries: number;
  maxRetries: number;
  error?: string;
};

export class UploadQueue {
  items: UploadItem[] = [];

  enqueue(files: Array<{ name: string; size: number }>) {
    for (const f of files) {
      this.items.push({
        id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
        filename: f.name,
        sizeBytes: f.size,
        status: "queued",
        progress: 0,
        etaSec: Math.max(1, Math.round(f.size / 200_000)),
        retries: 0,
        maxRetries: 3,
      });
    }
  }

  snapshot() {
    const total = this.items.length;
    const done = this.items.filter((i) => i.status === "done").length;
    const failed = this.items.filter((i) => i.status === "failed").length;
    const uploading = this.items.filter((i) => i.status === "uploading").length;
    return { total, done, failed, uploading, remaining: total - done - failed };
  }
}
