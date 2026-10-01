import { useQuery } from "@tanstack/react-query";

import { api } from "./api";

export type JobStatus = "draft" | "running" | "paused" | "completed" | "cancelled";
export type PostStatus = "pending" | "running" | "done" | "not_available" | "failed" | "cancelled";
export type AuthorMode = "full" | "name" | "anon";

export interface PermalinkLine {
  line: number;
  raw: string;
  status: string;
  url: string | null;
  platform: string | null;
  message: string | null;
}

export interface PreviewResult {
  total: number;
  new: number;
  duplicate: number;
  invalid: number;
  unsupported: number;
  lines: PermalinkLine[];
}

export interface Job {
  id: number;
  name: string;
  status: JobStatus;
  priority: number;
  max_comments: number;
  include_replies: boolean;
  max_replies_per_comment: number;
  sort: string;
  proxy_pool: string | null;
  proxy_kind: string | null;
  max_attempts: number;
  time_budget_sec: number;
  max_parallel: number;
  author_mode: AuthorMode;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  posts: number;
  pending: number;
  running: number;
  done: number;
  not_available: number;
  failed: number;
  cancelled: number;
  comments: number;
}

export interface PostRow {
  id: number;
  url: string;
  platform: string;
  status: PostStatus;
  attempts: number;
  not_before: string | null;
  last_error: string | null;
  comments_count: number;
  comments_reported: number | null;
  complete: boolean;
  stop_reason: string | null;
  worker_id: string | null;
  proxy_id: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface CommentRow {
  id: number;
  post_id: number;
  post_url: string;
  external_id: string;
  parent_external_id: string | null;
  text: string;
  author: string;
  author_id: string | null;
  author_url: string | null;
  time: string | null;
  time_raw: string | null;
  likes: number | null;
  likes_raw: string | null;
  reply_count: number | null;
  first_seen_at: string;
}

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  draft: "Nháp",
  running: "Đang chạy",
  paused: "Tạm dừng",
  completed: "Xong",
  cancelled: "Đã huỷ",
};

export const POST_STATUS_LABEL: Record<PostStatus, string> = {
  pending: "Đang chờ",
  running: "Đang đọc",
  done: "Đã đọc",
  not_available: "Không công khai",
  failed: "Lỗi",
  cancelled: "Đã huỷ",
};

export const jobsApi = {
  preview: (text: string) => api.post<PreviewResult>("/api/jobs/preview", { text }),
  create: (body: Record<string, unknown>) => api.post<Job>("/api/jobs", body),
  list: () => api.get<{ items: Job[]; total: number }>("/api/jobs", { query: { limit: 100 } }),
  get: (id: number) => api.get<Job>(`/api/jobs/${id}`),
  posts: (id: number) => api.get<{ items: PostRow[]; total: number }>(`/api/jobs/${id}/posts`, { query: { limit: 200 } }),
  comments: (id: number, query: { q?: string; after_id?: number; post_id?: number }) =>
    api.get<{ items: CommentRow[]; next_after_id: number | null }>(`/api/jobs/${id}/comments`, { query }),
  pause: (id: number) => api.post<Job>(`/api/jobs/${id}/pause`),
  resume: (id: number) => api.post<Job>(`/api/jobs/${id}/resume`),
  cancel: (id: number) => api.post<Job>(`/api/jobs/${id}/cancel`),
  retryFailed: (id: number) => api.post<Job>(`/api/jobs/${id}/retry-failed`),
};

export function useJobs(busy: boolean) {
  return useQuery({
    queryKey: ["jobs"],
    queryFn: jobsApi.list,
    refetchInterval: busy ? 2000 : 10000,
  });
}
