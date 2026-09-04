"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { SectionTitle } from "@/components/ui";
import type { CallOutcome } from "@/lib/analyzeCall";
import { addCall } from "@/lib/store";

const SAMPLE = `Sale: Em chào anh Minh, em Lan bên Bảo Việt. Anh đang tiện nói chuyện khoảng 30 giây không ạ?
Khách: Ừ, nói nhanh đi.
Sale: Em gọi vì chương trình chăm sóc sức khỏe đang có ưu đãi khám miễn phí. Anh đang có bảo hiểm sức khỏe chưa ạ?
Khách: Có của công ty rồi.
Sale: Bảo hiểm công ty thường có trần nằm viện. Em đối chiếu giúp điểm trống — nếu trùng em không tư vấn thêm.
Khách: Để xem giá nào.
Sale: Gói cơ bản khoảng 15 nghìn/ngày, chi trả nằm viện đến 200 triệu, kèm ưu đãi khám miễn phí tháng này.
Khách: Nghe cũng được.
Sale: Anh cho em mã CCCD để giữ chỗ ưu đãi hôm nay, em gửi link đăng ký luôn nhé?
Khách: Ok em, gửi đi.`;

export default function AnalyzePage() {
  const router = useRouter();
  const [title, setTitle] = useState("Cuộc gọi mới");
  const [industry, setIndustry] = useState("Bảo hiểm");
  const [product, setProduct] = useState("");
  const [agentName, setAgentName] = useState("Sale A");
  const [outcome, setOutcome] = useState<CallOutcome>("unknown");
  const [durationSec, setDurationSec] = useState(300);
  const [transcript, setTranscript] = useState(SAMPLE);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const call = addCall({
      title,
      industry,
      product: product || "Chưa đặt tên sản phẩm",
      agentName,
      outcome,
      durationSec,
      transcript,
    });
    router.push(`/calls/${call.id}`);
  }

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Phân tích cuộc gọi mới"
        subtitle="Dán transcript (Sale:/Khách:). Sau này gắn Whisper STT từ file ghi âm."
      />
      <form onSubmit={onSubmit} className="space-y-4 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Tiêu đề</span>
            <input className="mt-1 w-full rounded-xl border border-[var(--line)] bg-white px-3 py-2" value={title} onChange={(e) => setTitle(e.target.value)} required />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Ngành</span>
            <select className="mt-1 w-full rounded-xl border border-[var(--line)] bg-white px-3 py-2" value={industry} onChange={(e) => setIndustry(e.target.value)}>
              <option>Bảo hiểm</option>
              <option>Thực phẩm chức năng</option>
              <option>Điện máy / gia dụng</option>
              <option>Giáo dục / khóa học</option>
              <option>Bất động sản</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Sản phẩm</span>
            <input className="mt-1 w-full rounded-xl border border-[var(--line)] bg-white px-3 py-2" value={product} onChange={(e) => setProduct(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Tên sale</span>
            <input className="mt-1 w-full rounded-xl border border-[var(--line)] bg-white px-3 py-2" value={agentName} onChange={(e) => setAgentName(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Kết quả</span>
            <select className="mt-1 w-full rounded-xl border border-[var(--line)] bg-white px-3 py-2" value={outcome} onChange={(e) => setOutcome(e.target.value as CallOutcome)}>
              <option value="won">Chốt được</option>
              <option value="lost">Mất đơn</option>
              <option value="callback">Gọi lại</option>
              <option value="unknown">Chưa rõ</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Thời lượng (giây)</span>
            <input type="number" min={30} className="mt-1 w-full rounded-xl border border-[var(--line)] bg-white px-3 py-2" value={durationSec} onChange={(e) => setDurationSec(Number(e.target.value) || 60)} />
          </label>
        </div>
        <label className="block text-sm">
          <span className="text-[var(--muted)]">Transcript</span>
          <textarea className="mt-1 min-h-[280px] w-full rounded-xl border border-[var(--line)] bg-white px-3 py-2 font-mono text-sm" value={transcript} onChange={(e) => setTranscript(e.target.value)} required />
        </label>
        <button type="submit" className="rounded-full bg-[var(--accent)] px-5 py-2.5 text-sm font-medium text-white">
          Phân tích ngay
        </button>
      </form>
    </div>
  );
}
