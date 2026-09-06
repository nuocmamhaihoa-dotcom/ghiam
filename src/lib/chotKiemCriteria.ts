/**
 * Rubric ported from ChốtKiểm (222.255.215.55) — tra ghi âm & chấm tiêu chí.
 * Core checklist used on their lookup/sale report screens.
 */

export type CriteriaKey =
  | "greeting"
  | "productName"
  | "quantity"
  | "price"
  | "deliveryAddress"
  | "customerAgreed"
  | "customerRefused"
  | "customerAttitude"
  | "agentAttitude";

export type CriterionResult = {
  key: CriteriaKey;
  label: string;
  shortLabel: string;
  passed: boolean;
  value: string;
  evidence: string;
  required: boolean;
};

export type CloseOutcome =
  | "closed_explicit"
  | "closed_price_ok"
  | "follow_up"
  | "refused"
  | "needs_review"
  | "not_applicable";

export type ChotKiemScorecard = {
  criteria: CriterionResult[];
  passedCount: number;
  requiredCount: number;
  requiredPassed: number;
  passRate: number;
  closeOutcome: CloseOutcome;
  closeOutcomeLabel: string;
  complete: boolean;
  summary: string;
};

const LABELS: Record<CriteriaKey, { label: string; short: string; required: boolean }> = {
  greeting: { label: "1. Chào khách hàng", short: "1.Chào KH", required: true },
  productName: { label: "2. Giới thiệu tên sản phẩm", short: "2.Tên SP", required: true },
  quantity: { label: "3. Chốt số lượng sản phẩm", short: "3.Số lượng", required: true },
  price: { label: "4. Chốt giá bán sản phẩm", short: "4.Giá bán", required: true },
  deliveryAddress: { label: "5. Chốt rõ địa chỉ khách hàng", short: "5.Địa chỉ", required: true },
  customerAgreed: { label: "6. Khách hàng đồng ý nhận hàng", short: "6.Đồng ý nhận", required: true },
  customerRefused: { label: "7. Khách có từ chối nhận hàng không?", short: "7.Có từ chối?", required: false },
  customerAttitude: { label: "8. Thái độ khách hàng", short: "8.Thái độ KH", required: false },
  agentAttitude: { label: "9. Thái độ nhân viên", short: "9.Thái độ NV", required: true },
};

const CLOSE_LABELS: Record<CloseOutcome, string> = {
  closed_explicit: "Chốt đơn rõ ràng",
  closed_price_ok: "Đồng ý giá / nhận hàng",
  follow_up: "Chưa chốt — cần theo dõi",
  refused: "Khách từ chối",
  needs_review: "Cần nghe lại",
  not_applicable: "Không chấm",
};

function stripSpeaker(line: string): string {
  return line.replace(/^(sale|agent|tvv|telesale|em|khách|customer|khách hàng|kh)\s*[:\-]\s*/i, "");
}

function linesOf(transcript: string): string[] {
  return transcript
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

function findEvidence(transcript: string, re: RegExp): string {
  for (const line of linesOf(transcript)) {
    const text = stripSpeaker(line);
    if (re.test(text)) return text.slice(0, 160);
  }
  return "";
}

function scoreOne(key: CriteriaKey, transcript: string): CriterionResult {
  const meta = LABELS[key];
  const full = transcript.toLowerCase();

  switch (key) {
    case "greeting": {
      const re = /(?:em )?chào (?:anh|chị|bạn)|xin chào|em .* bên /i;
      const hit = findEvidence(transcript, re);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: Boolean(hit),
        value: hit ? "Có chào hỏi" : "Thiếu chào hỏi",
        evidence: hit,
        required: meta.required,
      };
    }
    case "productName": {
      const re = /(?:sản phẩm|gói|máy|khóa|căn|collagen|bảo hiểm|viên|nồi|omega|ielts)\b/i;
      const hit = findEvidence(transcript, re);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: Boolean(hit),
        value: hit ? hit.slice(0, 60) : "Chưa nêu tên SP",
        evidence: hit,
        required: meta.required,
      };
    }
    case "quantity": {
      const re = /\b(\d+)\s*(hộp|gói|cái|chiếc|suất|căn|liệu trình|tháng)\b|số lượng/i;
      const hit = findEvidence(transcript, re);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: Boolean(hit),
        value: hit ? hit.slice(0, 60) : "Chưa chốt số lượng",
        evidence: hit,
        required: meta.required,
      };
    }
    case "price": {
      const re = /(\d+[\d\.,]*)\s*(nghìn|ngàn|k\b|đ|vnđ|đồng|triệu)|giá|phí/i;
      const hit = findEvidence(transcript, re);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: Boolean(hit),
        value: hit ? hit.slice(0, 60) : "Chưa chốt giá",
        evidence: hit,
        required: meta.required,
      };
    }
    case "deliveryAddress": {
      const re = /địa chỉ|giao tới|ship|số nhà|phường|quận|huyện|thành phố|cccd|cmnd|mã\s*cccd/i;
      const hit = findEvidence(transcript, re);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: Boolean(hit),
        value: hit ? hit.slice(0, 60) : "Chưa chốt địa chỉ/định danh",
        evidence: hit,
        required: meta.required,
      };
    }
    case "customerAgreed": {
      const agreeRe = /ok em|được em|đồng ý|chốt đi|gửi link|lấy luôn|đăng ký|nhận hàng|gửi đi/i;
      const hit = findEvidence(transcript, agreeRe);
      const passed = Boolean(hit) || /ok em|chốt đi|gửi link|đồng ý/.test(full);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed,
        value: passed ? "Đồng ý nhận hàng" : "Chưa xác nhận",
        evidence: hit,
        required: meta.required,
      };
    }
    case "customerRefused": {
      // In ChốtKiểm: passed=true means "Không từ chối" (good)
      const refuseRe = /không lấy|không mua|thôi em|đừng gọi|từ chối|không nhận|không quan tâm|gác máy/i;
      const softNoRefuse = /không từ chối|không có từ chối/i;
      const refused = refuseRe.test(full) && !softNoRefuse.test(full);
      const hit = findEvidence(transcript, refuseRe);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: !refused,
        value: refused ? "Có từ chối" : "Không từ chối",
        evidence: hit,
        required: meta.required,
      };
    }
    case "customerAttitude": {
      const neg = /giận|bực|tức|mày|cút|im đi/i.test(full);
      const pos = /cảm ơn|ok em|được|vâng|dạ/i.test(full);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: !neg,
        value: neg ? "Tiêu cực" : pos ? "Tích cực" : "Trung lập",
        evidence: "",
        required: meta.required,
      };
    }
    case "agentAttitude": {
      const polite = /dạ|ạ\b|em chào|xin lỗi|cảm ơn anh|cảm ơn chị/i.test(full);
      const rude = /mày|im đi|không thì thôi/i.test(full);
      return {
        key,
        label: meta.label,
        shortLabel: meta.short,
        passed: polite && !rude,
        value: rude ? "Kém chuyên nghiệp" : polite ? "Chuyên nghiệp" : "Trung bình",
        evidence: "",
        required: meta.required,
      };
    }
    default: {
      const _exhaustive: never = key;
      return _exhaustive;
    }
  }
}

function deriveCloseOutcome(criteria: CriterionResult[]): CloseOutcome {
  const refused = criteria.find((c) => c.key === "customerRefused");
  const agreed = criteria.find((c) => c.key === "customerAgreed");
  const price = criteria.find((c) => c.key === "price");
  if (refused && !refused.passed) return "refused";
  if (agreed?.passed && price?.passed) return "closed_explicit";
  if (agreed?.passed) return "closed_price_ok";
  if (!agreed?.passed && refused?.passed) return "follow_up";
  return "needs_review";
}

export function scoreChotKiemTranscript(transcript: string): ChotKiemScorecard {
  const keys = Object.keys(LABELS) as CriteriaKey[];
  const criteria = keys.map((k) => scoreOne(k, transcript));
  const required = criteria.filter((c) => c.required);
  const requiredPassed = required.filter((c) => c.passed).length;
  const passedCount = criteria.filter((c) => c.passed).length;
  const closeOutcome = deriveCloseOutcome(criteria);
  const complete = requiredPassed === required.length && required.length > 0;

  return {
    criteria,
    passedCount,
    requiredCount: required.length,
    requiredPassed,
    passRate: Math.round((passedCount / Math.max(criteria.length, 1)) * 100),
    closeOutcome,
    closeOutcomeLabel: CLOSE_LABELS[closeOutcome],
    complete,
    summary: complete
      ? `Đủ tiêu chí bắt buộc (${requiredPassed}/${required.length}) · ${CLOSE_LABELS[closeOutcome]}`
      : `Thiếu tiêu chí bắt buộc (${requiredPassed}/${required.length}) · ${CLOSE_LABELS[closeOutcome]}`,
  };
}

/** Feature map discovered on ChốtKiểm VPS (for integration roadmap). */
export const CHOTKIEM_FEATURE_MAP = [
  { id: "lookup", name: "Tra cứu theo ngày / sale-results", api: "/api/lookup/day, /api/lookup/sale-results" },
  { id: "criteria", name: "Chấm 6–9 tiêu chí QA", api: "/api/calls/criteria-report" },
  { id: "stt", name: "Whisper đa nhà cung cấp + local", api: "/api/settings/speech" },
  { id: "ity", name: "Đồng bộ ghi âm ITY", api: "/api/sync/ity/*" },
  { id: "learning", name: "Học từ sửa tay / rescore", api: "/api/learning/*" },
  { id: "customers", name: "Kho khách / blacklist", api: "/api/customers/*" },
  { id: "monitor", name: "Giám sát realtime", api: "/api/monitor/*" },
  { id: "insights", name: "Insights + risk-queue", api: "/api/insights, /api/intelligence/risk-queue" },
] as const;
