import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Button } from "../components/ui/Button";
import { Field, Input, Select, Textarea } from "../components/ui/form";
import { errorMessage } from "../lib/api";
import { JOB_STATUS_LABEL, jobsApi, useJobs, type AuthorMode, type PreviewResult } from "../lib/jobs";

export function JobsPage() {
  const queryClient = useQueryClient();
  const jobs = useJobs(true);
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [maxComments, setMaxComments] = useState(200);
  const [authorMode, setAuthorMode] = useState<AuthorMode>("full");
  const [pool, setPool] = useState("");
  const [preview, setPreview] = useState<PreviewResult | null>(null);

  const previewMutation = useMutation({
    mutationFn: () => jobsApi.preview(text),
    onSuccess: setPreview,
    onError: (error) => toast.error(errorMessage(error)),
  });
  const createMutation = useMutation({
    mutationFn: () =>
      jobsApi.create({
        text,
        name: name.trim() || null,
        max_comments: maxComments,
        author_mode: authorMode,
        proxy_pool: pool.trim() || null,
        include_replies: true,
      }),
    onSuccess: async (job) => {
      toast.success(`Đã tạo job “${job.name}” với ${job.posts} bài`);
      setPreview(null);
      setText("");
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Job quét comment</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          Dán permalink bài viết công khai trên Facebook. Máy PC mở bài bằng Chromium qua proxy và đọc comment đang
          hiện, không đăng nhập và không giải captcha. Bài bị tường đăng nhập được đánh dấu bị chặn rồi thử proxy khác.
        </p>
      </div>

      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <Field label="Danh sách permalink" htmlFor="permalinks" hint="Mỗi dòng một link bài viết.">
          <Textarea
            id="permalinks"
            rows={8}
            value={text}
            onChange={(event) => { setText(event.target.value); }}
            placeholder="https://www.facebook.com/trang/posts/1234567890"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Tên job" htmlFor="job-name">
            <Input
              id="job-name"
              value={name}
              onChange={(event) => { setName(event.target.value); }}
              placeholder="Để trống thì lấy ngày giờ"
            />
          </Field>
          <Field label="Số comment tối đa mỗi bài" htmlFor="max-comments">
            <Input
              id="max-comments"
              type="number"
              min={1}
              max={5000}
              value={maxComments}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (Number.isInteger(value)) {
                  setMaxComments(value);
                }
              }}
            />
          </Field>
          <Field label="Thông tin tác giả" htmlFor="author-mode">
            <Select
              id="author-mode"
              value={authorMode}
              onChange={(event) => {
                setAuthorMode(event.target.value as AuthorMode);
              }}
            >
              <option value="full">Tên, mã và đường dẫn</option>
              <option value="name">Chỉ tên</option>
              <option value="anon">Ẩn danh</option>
            </Select>
          </Field>
        </div>
        <Field label="Pool proxy" htmlFor="pool" hint="Để trống thì dùng mọi proxy đang sống.">
          <Input id="pool" value={pool} onChange={(event) => { setPool(event.target.value); }} placeholder="4g-viettel" />
        </Field>
        {preview ? (
          <p className="text-sm text-slate-600">
            Xem trước: {preview.new} bài mới, {preview.duplicate} trùng, {preview.invalid} không phải link bài,{" "}
            {preview.unsupported} nền tảng khác.
          </p>
        ) : null}
        <div className="flex gap-2">
          <Button variant="secondary" loading={previewMutation.isPending} onClick={() => { previewMutation.mutate(); }} disabled={!text.trim()}>
            Xem trước
          </Button>
          <Button variant="primary" loading={createMutation.isPending} onClick={() => { createMutation.mutate(); }} disabled={!text.trim()}>
            Tạo job và chạy
          </Button>
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">Job</th>
              <th className="px-4 py-3 font-medium">Trạng thái</th>
              <th className="px-4 py-3 font-medium">Bài</th>
              <th className="px-4 py-3 font-medium">Comment</th>
            </tr>
          </thead>
          <tbody>
            {(jobs.data?.items ?? []).map((job) => (
              <tr key={job.id} className="border-t border-slate-100">
                <td className="px-4 py-3">
                  <Link to={`/jobs/${job.id}`} className="font-medium text-indigo-700 hover:underline">
                    {job.name}
                  </Link>
                </td>
                <td className="px-4 py-3">{JOB_STATUS_LABEL[job.status]}</td>
                <td className="px-4 py-3">
                  {job.done}/{job.posts}
                  {job.failed ? `, ${job.failed} lỗi` : ""}
                </td>
                <td className="px-4 py-3">{job.comments}</td>
              </tr>
            ))}
            {jobs.data?.items.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                  Chưa có job nào.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </div>
  );
}
