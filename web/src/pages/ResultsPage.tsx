import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import { Button } from "../components/ui/Button";
import { Field, Input, Select } from "../components/ui/form";
import { api, errorMessage } from "../lib/api";
import { saveBlob } from "../lib/download";
import { jobsApi, useJobs, type CommentRow } from "../lib/jobs";

interface MorePage {
  key: string;
  items: CommentRow[];
  cursor: number | null;
}

export function ResultsPage() {
  const [params, setParams] = useSearchParams();
  const selected = Number(params.get("job") ?? "");
  const jobId = Number.isInteger(selected) && selected > 0 ? selected : null;
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [more, setMore] = useState<MorePage | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const jobs = useJobs(false);
  const comments = useQuery({
    queryKey: ["comments", jobId, submitted],
    enabled: jobId !== null,
    queryFn: () => jobsApi.comments(jobId ?? 0, { q: submitted || undefined }),
  });
  const pageKey = `${jobId ?? 0}:${submitted}`;
  const appended = more?.key === pageKey ? more.items : [];
  const nextCursor = more?.key === pageKey ? more.cursor : (comments.data?.next_after_id ?? null);

  async function loadMore() {
    const cursor = nextCursor;
    const keyNow = pageKey;
    if (jobId === null || cursor == null || loadingMore) {
      return;
    }
    setLoadingMore(true);
    try {
      const page = await jobsApi.comments(jobId, { q: submitted || undefined, after_id: cursor });
      setMore((current) => ({
        key: keyNow,
        items: [...(current?.key === keyNow ? current.items : []), ...page.items],
        cursor: page.next_after_id,
      }));
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setLoadingMore(false);
    }
  }

  async function download(format: "csv" | "json" | "ndjson") {
    if (jobId === null) {
      return;
    }
    try {
      const file = await api.download(`/api/jobs/${jobId}/export`, { format });
      saveBlob(file.blob, file.filename ?? `comments-job-${jobId}.${format === "ndjson" ? "ndjson" : format}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const rows = [...(comments.data?.items ?? []), ...appended];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Kết quả comment</h1>
        <p className="mt-1 text-sm text-slate-500">
          Comment công khai đã đọc được từ bài viết công khai. Bốn cột chính là nội dung, tác giả, thời điểm và lượt thích.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Job" htmlFor="result-job" className="min-w-64">
          <Select
            id="result-job"
            value={jobId ?? ""}
            onChange={(event) => {
              setMore(null);
              setParams(event.target.value ? { job: event.target.value } : {});
            }}
          >
            <option value="">Chọn job</option>
            {(jobs.data?.items ?? []).map((job) => (
              <option key={job.id} value={job.id}>
                {job.name} ({job.comments})
              </option>
            ))}
          </Select>
        </Field>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setMore(null);
            setSubmitted(query.trim());
          }}
        >
          <Field label="Tìm trong nội dung hoặc tên" htmlFor="comment-q">
            <Input id="comment-q" value={query} onChange={(event) => { setQuery(event.target.value); }} />
          </Field>
          <Button type="submit" variant="secondary">
            Tìm
          </Button>
        </form>
        <Button
          variant="secondary"
          disabled={jobId === null}
          onClick={() => {
            void download("csv");
          }}
        >
          Tải CSV
        </Button>
        <Button
          variant="secondary"
          disabled={jobId === null}
          onClick={() => {
            void download("json");
          }}
        >
          Tải JSON
        </Button>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">Tác giả</th>
              <th className="px-4 py-3 font-medium">Nội dung</th>
              <th className="px-4 py-3 font-medium">Thời điểm</th>
              <th className="px-4 py-3 font-medium">Lượt thích</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-t border-slate-100 align-top">
                <td className="px-4 py-3 whitespace-nowrap">{row.author}</td>
                <td className="px-4 py-3">{row.text}</td>
                <td className="px-4 py-3 whitespace-nowrap">{row.time ?? row.time_raw ?? "—"}</td>
                <td className="px-4 py-3">{row.likes ?? row.likes_raw ?? "—"}</td>
              </tr>
            ))}
            {jobId !== null && rows.length === 0 && !comments.isLoading ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                  Chưa có comment nào khớp.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {comments.isError ? <p className="text-sm text-rose-600">{errorMessage(comments.error)}</p> : null}
      {nextCursor != null ? (
        <Button variant="secondary" loading={loadingMore} onClick={() => void loadMore()}>
          Tải thêm
        </Button>
      ) : null}
    </div>
  );
}
