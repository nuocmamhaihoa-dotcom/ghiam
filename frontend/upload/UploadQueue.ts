/**
 * Bulk upload queue for Audio Intelligence Engine.
 * Supports 1000+ files with progress, ETA, and retry.
 */
export type UploadItemStatus =
  | "queued"
  | "uploading"
  | "done"
  | "failed"
  | "retrying";

export type UploadItem = {
  id: string;
  filename: string;
  sizeBytes: number;
  folderPath?: string;
  status: UploadItemStatus;
  progress: number;
  etaSec: number;
  retries: number;
  maxRetries: number;
  error?: string;
};

export type UploadSnapshot = {
  total: number;
  done: number;
  failed: number;
  uploading: number;
  remaining: number;
  progressPct: number;
  etaSec: number;
};

function uid(): string {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class UploadQueue {
  items: UploadItem[] = [];
  bytesPerSecEstimate = 200_000;

  enqueue(
    files: Array<{ name: string; size: number; webkitRelativePath?: string }>,
  ): void {
    for (const f of files) {
      const folderPath = f.webkitRelativePath
        ? f.webkitRelativePath.split("/").slice(0, -1).join("/")
        : undefined;
      this.items.push({
        id: uid(),
        filename: f.name,
        sizeBytes: f.size,
        folderPath: folderPath || undefined,
        status: "queued",
        progress: 0,
        etaSec: Math.max(1, Math.round(f.size / this.bytesPerSecEstimate)),
        retries: 0,
        maxRetries: 3,
      });
    }
  }

  /** Enqueue a whole folder selection (input webkitdirectory). */
  enqueueFolder(fileList: ArrayLike<{ name: string; size: number; webkitRelativePath?: string }>): void {
    this.enqueue(Array.from(fileList as Array<{ name: string; size: number; webkitRelativePath?: string }>));
  }

  markUploading(id: string): void {
    const item = this.items.find((i) => i.id === id);
    if (!item) return;
    item.status = "uploading";
  }

  updateProgress(id: string, progress: number): void {
    const item = this.items.find((i) => i.id === id);
    if (!item) return;
    item.progress = Math.max(0, Math.min(100, progress));
    const remaining = ((100 - item.progress) / 100) * item.sizeBytes;
    item.etaSec = Math.max(1, Math.round(remaining / this.bytesPerSecEstimate));
  }

  markDone(id: string): void {
    const item = this.items.find((i) => i.id === id);
    if (!item) return;
    item.status = "done";
    item.progress = 100;
    item.etaSec = 0;
  }

  markFailed(id: string, error?: string): void {
    const item = this.items.find((i) => i.id === id);
    if (!item) return;
    if (item.retries < item.maxRetries) {
      item.retries += 1;
      item.status = "retrying";
      item.error = error;
      return;
    }
    item.status = "failed";
    item.error = error || "upload_failed";
  }

  nextQueued(limit = 8): UploadItem[] {
    return this.items.filter((i) => i.status === "queued" || i.status === "retrying").slice(0, limit);
  }

  snapshot(): UploadSnapshot {
    const total = this.items.length;
    const done = this.items.filter((i) => i.status === "done").length;
    const failed = this.items.filter((i) => i.status === "failed").length;
    const uploading = this.items.filter((i) => i.status === "uploading").length;
    const remaining = total - done - failed;
    const progressPct = total === 0 ? 0 : Math.round((done / total) * 100);
    const etaSec = this.items
      .filter((i) => i.status === "queued" || i.status === "uploading" || i.status === "retrying")
      .reduce((acc, i) => acc + i.etaSec, 0);
    return { total, done, failed, uploading, remaining, progressPct, etaSec };
  }
}
