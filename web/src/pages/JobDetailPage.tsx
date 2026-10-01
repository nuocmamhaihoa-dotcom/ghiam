import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { toast } from "sonner";

import { Button } from "../components/ui/Button";
import { errorMessage } from "../lib/api";
import { JOB_STATUS_LABEL, POST_STATUS_LABEL, jobsApi } from "../lib/jobs";

export function JobDetailPage() {
  const params = useParams();
  const jobId = Number(params.jobId);
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => jobsApi.get(jobId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2000 : 10000),
  });
  const posts = useQuery({
    queryKey: ["job-posts", jobId],
    queryFn: () => jobsApi.posts(jobId),
    refetchInterval: job.data?.status === "running" ? 2000 : 10000,
  });

  const act = useMutation({
    mutationFn: (action: "pause" | "resume" | "cancel" | "retry") => {
      if (action === "pause") return jobsApi.pause(jobId);
      if (action === "resume") return jobsApi.resume(jobId);
      if (action === "cancel") return jobsApi.cancel(jobId);
      return jobsApi.retryFailed(jobId);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      await queryClient.invalidateQueries({ queryKey: ["job-posts", jobId] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (!Number.isInteger(jobId)) {
    return <p>Không tìm thấy job.</p>;
  }
  const current = job.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/jobs" className="text-sm text-indigo-700 hover:underline">
            Tất cả job
          </Link>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900">{current?.name ?? "Job"}</h1>
          {current ? (
            <p className="mt-1 text-sm text-slate-500">
              {JOB_STATUS_LABEL[current.status]} · {current.done}/{current.posts} bài đã đọc · {current.comments} comment
              {current.not_available ? ` · ${current.not_available} không công khai` : ""}
              {current.failed ? ` · ${current.failed} lỗi` : ""}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {current?.status === "running" ? (
            <Button variant="secondary" loading={act.isPending} onClick={() => { act.mutate("pause"); }}>
              Tạm dừng
            </Button>
          ) : null}
          {current?.status === "paused" ? (
            <Button variant="primary" loading={act.isPending} onClick={() => { act.mutate("resume"); }}>
              Tiếp tục
            </Button>
          ) : null}
          {current && current.failed > 0 && current.status !== "cancelled" ? (
            <Button variant="secondary" loading={act.isPending} onClick={() => { act.mutate("retry"); }}>
              Chạy lại bài lỗi
            </Button>
          ) : null}
          {current && current.status !== "completed" && current.status !== "cancelled" ? (
            <Button variant="danger" loading={act.isPending} onClick={() => { act.mutate("cancel"); }}>
              Huỷ
            </Button>
          ) : null}
          <Link to={`/results?job=${jobId}`}>
            <Button variant="primary">Xem comment</Button>
          </Link>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">Bài viết</th>
              <th className="px-4 py-3 font-medium">Trạng thái</th>
              <th className="px-4 py-3 font-medium">Lần thử</th>
              <th className="px-4 py-3 font-medium">Comment</th>
              <th className="px-4 py-3 font-medium">Máy PC</th>
            </tr>
          </thead>
          <tbody>
            {(posts.data?.items ?? []).map((post) => (
              <tr key={post.id} className="border-t border-slate-100 align-top">
                <td className="max-w-md px-4 py-3 break-all">
                  <a href={post.url} className="text-indigo-700 hover:underline" target="_blank" rel="noreferrer">
                    {post.url}
                  </a>
                  {post.last_error ? <p className="mt-1 text-xs text-rose-600">{post.last_error}</p> : null}
                </td>
                <td className="px-4 py-3">{POST_STATUS_LABEL[post.status]}</td>
                <td className="px-4 py-3">{post.attempts}</td>
                <td className="px-4 py-3">{post.comments_count}</td>
                <td className="px-4 py-3">
                  {post.worker_id ?? "—"}
                  {post.proxy_id ? ` · proxy #${post.proxy_id}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
