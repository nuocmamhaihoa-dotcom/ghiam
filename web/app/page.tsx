"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Dashboard = {
  jobs_total: number;
  profiles_total: number;
  contacts_total: number;
  verified_profiles: number;
  avg_confidence: number;
  contacts_by_kind: Record<string, number>;
  jobs_by_status: Record<string, number>;
  policy: string;
};

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Dashboard>("/api/dashboard")
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Dữ liệu công khai</h1>
      <p className="lede">
        Thống kê hồ sơ TikTok đã quét từ trang công khai, cùng liên hệ trích từ bio, link bio và OCR ảnh đại diện.
      </p>
      <div className="policy">
        Chỉ dùng dữ liệu công khai. Công cụ không tra cứu số điện thoại sang TikTok ID và không gọi API đồng bộ danh bạ riêng tư.
      </div>
      {error && <p className="error">Không tải được dashboard: {error}</p>}
      {data && (
        <>
          <section className="grid">
            <article className="card"><div className="label">Hồ sơ</div><strong>{data.profiles_total}</strong></article>
            <article className="card"><div className="label">Liên hệ</div><strong>{data.contacts_total}</strong></article>
            <article className="card"><div className="label">Đã xác minh</div><strong>{data.verified_profiles}</strong></article>
            <article className="card"><div className="label">Độ tin cậy TB</div><strong>{data.avg_confidence.toFixed(2)}</strong></article>
          </section>
          <section className="panel">
            <h2>Liên hệ theo loại</h2>
            <table>
              <thead><tr><th>Loại</th><th>Số lượng</th></tr></thead>
              <tbody>
                {Object.entries(data.contacts_by_kind).map(([kind, count]) => (
                  <tr key={kind}><td>{kind}</td><td>{count}</td></tr>
                ))}
                {Object.keys(data.contacts_by_kind).length === 0 && (
                  <tr><td colSpan={2}>Chưa có liên hệ. Hãy tạo một phiên quét.</td></tr>
                )}
              </tbody>
            </table>
            <p>Job: {data.jobs_total}. Trạng thái: {JSON.stringify(data.jobs_by_status)}. Chính sách: {data.policy}.</p>
          </section>
        </>
      )}
    </>
  );
}
