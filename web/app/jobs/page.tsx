"use client";

import { useEffect, useState } from "react";
import { api, downloadUrl } from "../../lib/api";

type Job = { id: string; name: string; status: string; checkpoint: Record<string, number> };
type Target = { id: string; raw_input: string; username: string | null; status: string; last_error: string | null };

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [name, setName] = useState("Đợt quét");
  const [lines, setLines] = useState("@publicshop\nhttps://www.tiktok.com/@examplecreator");
  const [selected, setSelected] = useState<string | null>(null);
  const [targets, setTargets] = useState<Target[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  async function refresh() {
    setJobs(await api<Job[]>("/api/jobs"));
  }

  useEffect(() => {
    refresh().catch((err: Error) => setMessage(err.message));
  }, []);

  async function createAndImport() {
    setMessage(null);
    const job = await api<Job>("/api/jobs", { method: "POST", body: JSON.stringify({ name }) });
    const imported = await api<{ accepted: unknown[]; rejected: { raw: string; reason: string }[] }>(
      `/api/jobs/${job.id}/targets`,
      { method: "POST", body: JSON.stringify({ lines: lines.split("\n") }) },
    );
    setSelected(job.id);
    setTargets(await api<Target[]>(`/api/jobs/${job.id}/targets`));
    setMessage(`Đã nhận ${imported.accepted.length} hồ sơ, từ chối ${imported.rejected.length}.`);
    await refresh();
  }

  async function loadTargets(jobId: string) {
    setSelected(jobId);
    setTargets(await api<Target[]>(`/api/jobs/${jobId}/targets`));
  }

  async function enqueue(jobId: string) {
    await api(`/api/jobs/${jobId}/enqueue`, { method: "POST" });
    setMessage("Đã đưa vào hàng đợi. Worker quét từng hồ sơ công khai, có checkpoint để resume.");
    await refresh();
  }

  return (
    <>
      <h1>Phiên quét</h1>
      <p className="lede">Nhập username hoặc URL hồ sơ công khai. URL video, For You, hoặc đường dẫn không phải /@username sẽ bị từ chối.</p>
      {message && <p>{message}</p>}
      <section className="panel">
        <div className="row">
          <input aria-label="Tên phiên" value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <textarea aria-label="Danh sách hồ sơ" value={lines} onChange={(event) => setLines(event.target.value)} />
        <div className="row">
          <button onClick={() => createAndImport().catch((err: Error) => setMessage(err.message))}>Tạo và import</button>
        </div>
      </section>
      <section className="panel">
        <table>
          <thead>
            <tr><th>Tên</th><th>Trạng thái</th><th>Checkpoint</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.name}</td>
                <td><span className="pill">{job.status}</span></td>
                <td>{job.checkpoint?.done ?? 0} xong / {job.checkpoint?.failed ?? 0} lỗi</td>
                <td className="row">
                  <button className="secondary" onClick={() => loadTargets(job.id)}>Xem</button>
                  <button onClick={() => enqueue(job.id).catch((err: Error) => setMessage(err.message))}>Xếp hàng</button>
                  <button className="secondary" onClick={() => api(`/api/jobs/${job.id}/pause`, { method: "POST" }).then(refresh)}>Tạm dừng</button>
                  <a className="button secondary" href={downloadUrl(`/api/jobs/${job.id}/export.csv`)}>CSV</a>
                  <a className="button secondary" href={downloadUrl(`/api/jobs/${job.id}/export.xlsx`)}>Excel</a>
                </td>
              </tr>
            ))}
            {jobs.length === 0 && <tr><td colSpan={4}>Chưa có phiên nào.</td></tr>}
          </tbody>
        </table>
        {selected && (
          <table>
            <thead><tr><th>Đầu vào</th><th>Username</th><th>Trạng thái</th><th>Lỗi</th></tr></thead>
            <tbody>
              {targets.map((target) => (
                <tr key={target.id}>
                  <td>{target.raw_input}</td>
                  <td>{target.username ?? "—"}</td>
                  <td>{target.status}</td>
                  <td>{target.last_error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
