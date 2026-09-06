import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/itySyncClient", () => ({
  processItyPendingDownloads: vi.fn(),
}));

vi.mock("@/lib/recordingLibrary", () => ({
  importAndAnalyzeCallIds: vi.fn(),
}));

import { processItyPendingDownloads } from "@/lib/itySyncClient";
import { importAndAnalyzeCallIds } from "@/lib/recordingLibrary";
import { downloadThenAnalyzeCalls } from "@/lib/ityImmediateAnalyze";

describe("downloadThenAnalyzeCalls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("downloads with autoAnalyze then immediately deep-analyzes the same ids", async () => {
    vi.mocked(processItyPendingDownloads).mockResolvedValue({
      ok: true,
      processed: 2,
    });
    vi.mocked(importAndAnalyzeCallIds).mockResolvedValue({
      imported: 2,
      updated: 0,
      skipped: 0,
      withAudio: 2,
      analyzed: 2,
      errors: [],
      localTotal: 2,
    });

    const result = await downloadThenAnalyzeCalls({
      callIds: ["a", "b", "a"],
      concurrency: 2,
    });

    expect(processItyPendingDownloads).toHaveBeenCalledWith(
      expect.objectContaining({
        callIds: ["a", "b"],
        autoAnalyze: true,
        concurrency: 2,
      }),
    );
    expect(importAndAnalyzeCallIds).toHaveBeenCalledWith(["a", "b"], {
      downloadAudio: true,
    });
    expect(result.timedOut).toBe(false);
    expect(result.analyze.analyzed).toBe(2);
  });

  it("still analyzes locally when download times out", async () => {
    vi.mocked(processItyPendingDownloads).mockRejectedValue(
      new Error("ITY process-pending timeout — drain still running"),
    );
    vi.mocked(importAndAnalyzeCallIds).mockResolvedValue({
      imported: 1,
      updated: 0,
      skipped: 0,
      withAudio: 1,
      analyzed: 1,
      errors: [],
      localTotal: 1,
    });

    const result = await downloadThenAnalyzeCalls({
      callIds: ["x"],
    });

    expect(result.timedOut).toBe(true);
    expect(result.downloadError).toMatch(/timeout/i);
    expect(importAndAnalyzeCallIds).toHaveBeenCalledWith(["x"], {
      downloadAudio: true,
    });
    expect(result.analyze.analyzed).toBe(1);
  });
});
