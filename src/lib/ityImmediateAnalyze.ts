/**
 * After ITY audio download succeeds: immediately import into the local library
 * and persist full deep sales analysis (never leave downloads unanalyzed).
 */

import { processItyPendingDownloads } from "@/lib/itySyncClient";
import { importAndAnalyzeCallIds } from "@/lib/recordingLibrary";

export type DownloadThenAnalyzeResult = {
  callIds: string[];
  download: Record<string, unknown> | null;
  downloadError: string | null;
  timedOut: boolean;
  analyze: Awaited<ReturnType<typeof importAndAnalyzeCallIds>>;
};

/**
 * Process a pending ITY download batch (with ChốtKiểm autoAnalyze),
 * then immediately import + deep-analyze those callIds locally.
 * Even on timeout, still attempts local import/analyze for the requested ids
 * (some may have finished downloading in the background).
 */
export async function downloadThenAnalyzeCalls(input: {
  callIds: string[];
  concurrency?: number;
  timeoutMs?: number;
  /** Forward to ChốtKiểm process-pending (STT / remote analyze). Default true. */
  autoAnalyze?: boolean;
}): Promise<DownloadThenAnalyzeResult> {
  const callIds = [...new Set(input.callIds.map(String).filter(Boolean))];
  let download: Record<string, unknown> | null = null;
  let downloadError: string | null = null;
  let timedOut = false;

  if (callIds.length) {
    try {
      download = await processItyPendingDownloads({
        callIds,
        concurrency: input.concurrency ?? 3,
        timeoutMs: input.timeoutMs ?? 45_000,
        autoAnalyze: input.autoAnalyze !== false,
      });
    } catch (error) {
      timedOut =
        error instanceof Error &&
        /timeout/i.test(error.message);
      downloadError =
        error instanceof Error ? error.message : "process-pending failed";
    }
  }

  const analyze = await importAndAnalyzeCallIds(callIds, {
    downloadAudio: true,
  });

  return { callIds, download, downloadError, timedOut, analyze };
}
