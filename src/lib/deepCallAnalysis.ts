/**
 * Deep telesales-call analysis for coaching and later call recreation.
 * Input: transcript (+ optional ChốtKiểm scorecard / summary).
 */

import { scoreChotKiemTranscript } from "./chotKiemCriteria";

export const ANALYSIS_VERSION = 2 as const;

export type StageKey =
  | "opening"
  | "discovery"
  | "pitch"
  | "objection"
  | "close"
  | "outro";

export type Speaker = "agent" | "customer" | "unknown";

export type Turn = {
  index: number;
  tSec: number;
  speaker: Speaker;
  text: string;
  stage: StageKey;
  intent: string;
  signals: string[];
};

export type StageInfo = {
  key: StageKey;
  label: string;
  startSec: number;
  endSec: number;
  score: number;
  summary: string;
  keyLines: string[];
  tips: string[];
};

export type ObjectionInfo = {
  type: string;
  customerLine: string;
  agentReply: string;
  technique: string;
  handledWell: boolean;
  tip: string;
};

export type CloseInfo = {
  type: string;
  line: string;
  result: "won" | "soft" | "rejected" | "unclear";
};

export type RecreationStep = {
  stage: StageKey;
  goal: string;
  modelLine: string;
  why: string;
  alternatives: string[];
};


export type QualityMetric = {
  key: string;
  label: string;
  score: number;
  passed: boolean;
  weight: number;
  evidence: string;
  tip: string;
};

export type QualityScorecard = {
  overallScore: number;
  passRate: number;
  grade: "A" | "B" | "C" | "D";
  chotKiemComplete: boolean;
  closeOutcome: string;
  closeOutcomeLabel: string;
  metrics: QualityMetric[];
  requiredFailed: string[];
  strengths: string[];
  gaps: string[];
};

export type AiCloneSlot = {
  key: string;
  label: string;
  value: string | null;
  required: boolean;
  example: string;
};

export type AiCloneBranch = {
  trigger: string;
  customerLine: string;
  reply: string;
  tip: string;
};

export type AiClonePack = {
  cloneReady: boolean;
  cloneScore: number;
  persona: {
    tone: string;
    paceWpm: number;
    politeness: number;
    empathy: number;
    assertiveness: number;
    styleNotes: string;
  };
  slots: AiCloneSlot[];
  mustSay: string[];
  avoidSay: string[];
  variableScript: Array<{
    stage: string;
    goal: string;
    template: string;
    fillHints: string[];
  }>;
  objectionBranches: AiCloneBranch[];
  openers: string[];
  closers: string[];
  targetCustomerProfile: string;
  recreationNotes: string[];
};

export type DeepCallAnalysis = {
  version: typeof ANALYSIS_VERSION;
  analyzedAt: string;
  durationSec: number;
  outcome: "won" | "lost" | "callback" | "unknown";
  readinessScore: number;
  summary: {
    headline: string;
    whatWorked: string[];
    whatFailed: string[];
    oneThingToFix: string;
  };
  speakers: {
    agentTalkRatio: number;
    customerTalkRatio: number;
    turnCount: number;
    agentTurns: number;
    customerTurns: number;
    avgAgentTurnChars: number;
    avgCustomerTurnChars: number;
  };
  delivery: {
    speakingRateWpm: number;
    pauseRatio: number;
    politenessScore: number;
    empathyScore: number;
    urgencyScore: number;
    confidenceScore: number;
    toneLabel: string;
    toneNotes: string;
  };
  timeline: Turn[];
  stages: StageInfo[];
  opening: {
    greeting: string | null;
    permissionAsk: string | null;
    companyIntro: string | null;
    valueHook: string | null;
    score: number;
  };
  discoveryQuestions: string[];
  pitchPoints: string[];
  objections: ObjectionInfo[];
  closeAttempts: CloseInfo[];
  buyingSignals: string[];
  riskSignals: string[];
  persuasionTechniques: string[];
  extracted: {
    customerName: string | null;
    product: string | null;
    quantity: string | null;
    price: string | null;
    address: string | null;
    deliveryPromise: string | null;
    phoneMention: string | null;
  };
  recreationScript: RecreationStep[];
  qualityScorecard: QualityScorecard;
  aiClonePack: AiClonePack;
  chotKiem?: {
    grade?: string;
    overallScore?: number;
    isComplete?: boolean;
    criteria?: Array<{
      key: string;
      label?: string;
      passed: boolean;
      required?: boolean;
      value?: string | null;
      evidence?: string | null;
    }>;
  };
};

const STAGE_LABEL: Record<StageKey, string> = {
  opening: "Mở đầu",
  discovery: "Khai thác nhu cầu",
  pitch: "Giới thiệu / Pitch",
  objection: "Xử lý từ chối",
  close: "Chốt đơn",
  outro: "Kết thúc",
};

const OBJECTIONS: Array<{
  type: string;
  patterns: RegExp[];
  technique: string;
  tip: string;
  good: RegExp[];
}> = [
  {
    type: "Giá / ngân sách",
    patterns: [/đắt/i, /mắc/i, /giá cao/i, /không có tiền/i, /rẻ hơn/i],
    technique: "Tách giá + neo ưu đãi + hỏi ngân sách",
    tip: "Đổi giá thành chi phí/ngày, nêu lợi ích, hỏi ngân sách.",
    good: [/ưu đãi/i, /tiết kiệm/i, /trả góp/i, /ngân sách/i],
  },
  {
    type: "Đang dùng đối thủ",
    patterns: [/đang dùng/i, /đã có/i, /xài bên/i, /của công ty/i],
    technique: "Không đả kích đối thủ — hỏi điểm trống",
    tip: "Hỏi điểm chưa hài lòng rồi đề xuất khác biệt cụ thể.",
    good: [/hài lòng/i, /thiếu/i, /khác/i, /bổ sung/i],
  },
  {
    type: "Để suy nghĩ",
    patterns: [/suy nghĩ/i, /để xem/i, /cân nhắc/i, /mai nói/i],
    technique: "Chốt mềm + lý do hành động hôm nay",
    tip: "Tóm tắt lợi ích, tạo lý do hành động hôm nay, hỏi chốt mềm.",
    good: [/hôm nay/i, /giữ/i, /ưu đãi/i, /nếu hợp/i],
  },
  {
    type: "Không tin / nghi ngờ",
    patterns: [/lừa/i, /không tin/i, /thật không/i, /ảo/i],
    technique: "Bằng chứng xã hội + cam kết rõ",
    tip: "Đưa bảo hành/cam kết/hoàn tiền và ví dụ khách tương tự.",
    good: [/bảo hành/i, /cam kết/i, /hoàn tiền/i, /chính hãng/i],
  },
  {
    type: "Không nhu cầu",
    patterns: [/không cần/i, /không dùng/i, /không mua/i],
    technique: "Tái khung nhu cầu ẩn",
    tip: "Hỏi tình huống dùng gần nhất rồi gắn 1 lợi ích đúng pain.",
    good: [/trường hợp/i, /nếu/i, /khi nào/i, /thử/i],
  },
];

function clamp(n: number, min = 0, max = 100) {
  return Math.max(min, Math.min(max, Math.round(n)));
}

function linesOf(transcript: string): string[] {
  return transcript
    .split(/\r?\n/)
    .flatMap((line) => line.split(/(?<=[.!?…])\s+/))
    .map((l) => l.trim())
    .filter(Boolean);
}

function speakerOf(line: string): Speaker {
  if (/^(sale|nv|nhân viên|agent|em)\s*[:：]/i.test(line)) return "agent";
  if (/^(khách|customer|chị|anh|cô|chú|bác)\s*[:：]/i.test(line)) {
    return "customer";
  }
  if (/^(em |dạ em|vâng em|em chào|em xin)/i.test(line)) return "agent";
  if (/^(không |thôi |đắt|để |anh |chị )/i.test(line)) return "customer";
  return "unknown";
}

function stripSpeaker(line: string): string {
  return line
    .replace(
      /^(sale|nv|nhân viên|agent|em|khách|customer|chị|anh)\s*[:：]\s*/i,
      "",
    )
    .trim();
}

function stageOf(text: string, index: number, total: number): StageKey {
  const lower = text.toLowerCase();
  if (/chào|tiện nói|xin phép|em gọi/i.test(lower) && index < total * 0.25) {
    return "opening";
  }
  if (/nhu cầu|đang dùng|bao nhiêu|ở đâu|xã|phường|huyện|địa chỉ/i.test(lower)) {
    return "discovery";
  }
  if (/sản phẩm|hộp|gói|ưu đãi|giảm|tặng|bảo hành|giá|nghìn/i.test(lower)) {
    return "pitch";
  }
  if (/đắt|không cần|suy nghĩ|để xem|không mua|không tin|thôi/i.test(lower)) {
    return "objection";
  }
  if (/chốt|đăng ký|nhận hàng|đồng ý|thanh toán|tạo đơn/i.test(lower)) {
    return "close";
  }
  if (index > total * 0.9) return "outro";
  if (index < total * 0.2) return "opening";
  if (index < total * 0.45) return "discovery";
  if (index < total * 0.7) return "pitch";
  return "close";
}

function intentOf(text: string, stage: StageKey): string {
  if (/chào/i.test(text)) return "greeting";
  if (/tiện nói|xin phép/i.test(text)) return "permission";
  if (/\?|bao nhiêu|ở đâu|xã|phường/i.test(text)) return "question";
  if (/ưu đãi|giảm|tặng|bảo hành/i.test(text)) return "value_offer";
  if (/đồng ý|ok|được|chốt|nhận hàng/i.test(text)) return "agreement";
  if (/không|thôi|đắt|suy nghĩ/i.test(text)) return "resistance";
  if (stage === "close") return "close_attempt";
  return "statement";
}

function firstMatch(re: RegExp, text: string): string | null {
  const m = text.match(re);
  if (!m) return null;
  return (m[1] || m[0] || "").trim() || null;
}

export function analyzeCallDeep(input: {
  transcript: string;
  durationSec?: number;
  outcome?: "won" | "lost" | "callback" | "unknown";
  callSummary?: string;
  grade?: string;
  overallScore?: number;
  isComplete?: boolean;
  scorecard?: DeepCallAnalysis["chotKiem"];
}): DeepCallAnalysis {
  const raw = linesOf(input.transcript || "");
  const durationSec = Math.max(20, input.durationSec || Math.max(40, raw.length * 4));
  const outcome = input.outcome || "unknown";
  const full = input.transcript || "";

  const timeline: Turn[] = raw.map((line, index) => {
    const text = stripSpeaker(line);
    const stage = stageOf(text, index, raw.length);
    const signals: string[] = [];
    if (/ưu đãi|giảm|tặng/i.test(text)) signals.push("offer");
    if (/đồng ý|ok|được|nhận hàng|lấy/i.test(text)) signals.push("buy");
    if (/không|thôi|đắt|suy nghĩ/i.test(text)) signals.push("risk");
    if (/\?/i.test(text)) signals.push("question");
    return {
      index,
      tSec: Math.round((index / Math.max(raw.length - 1, 1)) * durationSec),
      speaker: speakerOf(line),
      text,
      stage,
      intent: intentOf(text, stage),
      signals,
    };
  });

  const agentTurns = timeline.filter((t) => t.speaker === "agent");
  const customerTurns = timeline.filter((t) => t.speaker === "customer");
  const agentChars = agentTurns.reduce((s, t) => s + t.text.length, 0);
  const customerChars = customerTurns.reduce((s, t) => s + t.text.length, 0);
  const totalChars = Math.max(agentChars + customerChars, 1);

  const words = Math.max(
    full.split(/\s+/).filter(Boolean).length,
    Math.round(full.replace(/\s+/g, "").length / 4),
  );
  const minutes = Math.max(durationSec / 60, 0.4);
  let speakingRateWpm = Math.round((words * 0.65) / minutes);
  if (speakingRateWpm < 70) speakingRateWpm = 110;
  if (speakingRateWpm > 200) speakingRateWpm = 175;

  const sentenceCount = (full.match(/[.!?…]+/g) ?? []).length || Math.max(raw.length, 1);
  const pauseRatio = clamp((sentenceCount / Math.max(words, 1)) * 100, 5, 40) / 100;

  const politenessScore = clamp(
    40 + (full.match(/ạ|dạ|anh|chị|em xin/gi) || []).length * 4,
  );
  const empathyScore = clamp(
    30 +
      (full.match(/hiểu|đúng rồi|cảm ơn|không sao|em hỗ trợ/gi) || []).length * 8,
  );
  const urgencyScore = clamp(
    (full.match(/hôm nay|nhanh|chỉ còn|ưu đãi|giữ chỗ/gi) || []).length * 12,
  );
  const confidenceScore = clamp(
    45 +
      (full.match(/cam kết|chính hãng|bảo hành|chắc chắn|rõ ràng/gi) || []).length *
        10 -
      (full.match(/à |ừm |kiểu /gi) || []).length * 3,
  );

  let toneLabel = "Trung tính";
  let toneNotes = "Giọng đều, chưa rõ điểm nhấn cảm xúc.";
  if (speakingRateWpm > 165) {
    toneLabel = "Nhanh / áp lực";
    toneNotes = "Tốc độ cao — dễ làm khách phòng thủ khi nói giá.";
  } else if (empathyScore >= 70 && politenessScore >= 70) {
    toneLabel = "Tự tin – đồng cảm";
    toneNotes = "Có lịch sự và dấu hiệu lắng nghe tốt.";
  } else if (politenessScore >= 70) {
    toneLabel = "Lịch sự – mềm";
    toneNotes = "Lịch sự tốt; cần chắc hơn khi chốt.";
  }

  const stageKeys: StageKey[] = [
    "opening",
    "discovery",
    "pitch",
    "objection",
    "close",
    "outro",
  ];
  const stages: StageInfo[] = stageKeys.map((key) => {
    const turns = timeline.filter((t) => t.stage === key);
    const keyLines = turns.slice(0, 4).map((t) => t.text).filter(Boolean);
    let score = turns.length ? 55 : 25;
    if (key === "opening" && /chào|tiện nói/i.test(keyLines.join(" "))) score += 20;
    if (key === "close" && /chốt|nhận hàng|đồng ý|đăng ký/i.test(keyLines.join(" "))) {
      score += 25;
    }
    if (key === "objection" && turns.length) score += 10;
    if (outcome === "won" && (key === "close" || key === "pitch")) score += 10;
    return {
      key,
      label: STAGE_LABEL[key],
      startSec: turns[0]?.tSec ?? 0,
      endSec: turns[turns.length - 1]?.tSec ?? 0,
      score: clamp(score),
      summary:
        turns.length === 0
          ? `Thiếu đoạn ${STAGE_LABEL[key]}.`
          : `Có ${turns.length} lượt thuộc ${STAGE_LABEL[key]}.`,
      keyLines,
      tips:
        turns.length === 0
          ? [`Bổ sung câu mẫu cho đoạn ${STAGE_LABEL[key]}.`]
          : [],
    };
  });

  const opening = {
    greeting: firstMatch(
      /(em chào[^.]{0,60}|chào anh[^.]{0,40}|chào chị[^.]{0,40})/i,
      full,
    ),
    permissionAsk: firstMatch(/(tiện nói[^?]{0,40}\?|xin phép[^?]{0,40}\?)/i, full),
    companyIntro: firstMatch(/(bên [^.?]{3,40}|em .+ bên [^.?]{3,40})/i, full),
    valueHook: firstMatch(/(ưu đãi[^.?]{0,50}|giảm[^.?]{0,40}|tặng[^.?]{0,40})/i, full),
    score: 0,
  };
  opening.score = clamp(
    (opening.greeting ? 25 : 0) +
      (opening.permissionAsk ? 25 : 0) +
      (opening.companyIntro ? 25 : 0) +
      (opening.valueHook ? 25 : 0),
  );

  const discoveryQuestions = timeline
    .filter(
      (t) =>
        t.stage === "discovery" &&
        /\?|ạ\?|bao nhiêu|ở đâu|xã|phường/i.test(t.text),
    )
    .map((t) => t.text)
    .slice(0, 8);

  const pitchPoints = timeline
    .filter(
      (t) => t.stage === "pitch" || /ưu đãi|tặng|bảo hành|hộp|gói|giá/i.test(t.text),
    )
    .map((t) => t.text)
    .filter((t, i, arr) => arr.indexOf(t) === i)
    .slice(0, 8);

  const objections: ObjectionInfo[] = [];
  for (let i = 0; i < timeline.length; i += 1) {
    const turn = timeline[i]!;
    for (const def of OBJECTIONS) {
      if (!def.patterns.some((re) => re.test(turn.text))) continue;
      const reply =
        timeline.slice(i + 1, i + 4).find((t) => t.speaker !== "customer")?.text ||
        "";
      objections.push({
        type: def.type,
        customerLine: turn.text,
        agentReply: reply || "(Không thấy phản hồi rõ)",
        technique: def.technique,
        handledWell: def.good.some((re) => re.test(reply)),
        tip: def.tip,
      });
    }
  }
  const uniqObjections = objections
    .filter(
      (o, idx, arr) =>
        arr.findIndex(
          (x) => x.type === o.type && x.customerLine === o.customerLine,
        ) === idx,
    )
    .slice(0, 8);

  const closeAttempts: CloseInfo[] = timeline
    .filter((t) =>
      /chốt|đăng ký|nhận hàng|gửi về|đồng ý|bao nhiêu hộp|thanh toán/i.test(t.text),
    )
    .slice(0, 6)
    .map((t) => {
      let result: CloseInfo["result"] = "unclear";
      if (/đồng ý|ok|được|nhận hàng giúp/i.test(t.text) || outcome === "won") {
        result = "won";
      } else if (/không|thôi|để/i.test(t.text) || outcome === "lost") {
        result = "rejected";
      } else if (/nếu|có thể|thử/i.test(t.text)) {
        result = "soft";
      }
      return {
        type: /nhận hàng|gửi về|địa chỉ/i.test(t.text)
          ? "confirm_order"
          : "ask_close",
        line: t.text,
        result,
      };
    });

  const buyingSignals = timeline
    .filter(
      (t) =>
        t.signals.includes("buy") ||
        /đồng ý|lấy|gửi|nhận hàng|đăng ký/i.test(t.text),
    )
    .map((t) => t.text)
    .slice(0, 8);
  const riskSignals = timeline
    .filter((t) => t.signals.includes("risk"))
    .map((t) => t.text)
    .slice(0, 8);

  const persuasionTechniques: string[] = [];
  if (/ưu đãi|hôm nay|chỉ còn/i.test(full)) {
    persuasionTechniques.push("Scarcity / ưu đãi có hạn");
  }
  if (/cam kết|bảo hành|hoàn tiền|chính hãng/i.test(full)) {
    persuasionTechniques.push("Risk reversal");
  }
  if (/nhiều khách|mọi người|hay dùng/i.test(full)) {
    persuasionTechniques.push("Social proof");
  }
  if (/nếu|giả sử|trường hợp/i.test(full)) {
    persuasionTechniques.push("Hypothetical framing");
  }
  if (/tiết kiệm|rẻ hơn/i.test(full)) {
    persuasionTechniques.push("Value reframing");
  }
  if (discoveryQuestions.length) {
    persuasionTechniques.push("Consultative questions");
  }

  const extracted = {
    customerName: firstMatch(/(?:anh|chị|cô|chú)\s+([\p{L}']{2,20})/u, full),
    product: firstMatch(
      /((?:hộp|gói|lọ)\s+[^.,\n]{3,40}|collagen|xà phòng|bột|kem|omega)[^.,\n]{0,30}/i,
      full,
    ),
    quantity: firstMatch(/(\d+\s*(?:hộp|gói|lọ|cái))/i, full),
    price: firstMatch(/(\d+[\d.]*\s*(?:k|nghìn|đ|đồng|vnđ))/i, full),
    address: firstMatch(/((?:xã|phường|huyện|tp|thành phố)[^.,\n]{3,50})/i, full),
    deliveryPromise: firstMatch(
      /((?:\d+\s*ngày|[^\n.]{0,20}nhận hàng)[^.?\n]{0,40})/i,
      full,
    ),
    phoneMention: firstMatch(/(0\d{8,10})/, full),
  };

  const recreationScript: RecreationStep[] = [
    {
      stage: "opening",
      goal: "Xin phép + định danh + hook",
      modelLine:
        opening.greeting ||
        "Em chào anh/chị, em [Tên] bên [Công ty]. Anh/chị đang tiện nghe 20–30 giây không ạ?",
      why: "Opening quyết định khách có cho phép pitch hay không.",
      alternatives: [
        "Em xin phép anh/chị 20 giây về ưu đãi hôm nay — nếu không hợp em ngắt máy liền ạ.",
      ],
    },
    {
      stage: "discovery",
      goal: "Hỏi đúng pain / ngữ cảnh",
      modelLine:
        discoveryQuestions[0] ||
        "Anh/chị đang dùng loại nào, và điểm nào chưa ổn nhất ạ?",
      why: "Discovery tạo đà pitch cá nhân hóa.",
      alternatives: discoveryQuestions.slice(1, 3),
    },
    {
      stage: "pitch",
      goal: "1 lợi ích + 1 bằng chứng + 1 ưu đãi",
      modelLine:
        pitchPoints[0] ||
        "Bên em đang có [SP] tặng kèm [quà], cam kết rõ — đúng case của anh/chị.",
      why: "Pitch ngắn, gắn pain đã hỏi.",
      alternatives: pitchPoints.slice(1, 3),
    },
    {
      stage: "objection",
      goal: "Xác nhận cảm xúc → bằng chứng → chốt mềm",
      modelLine:
        uniqObjections[0]?.agentReply ||
        "Em hiểu anh/chị đang cân nhắc. Nếu em làm rõ [điểm lo], anh/chị sẵn sàng thử hôm nay không ạ?",
      why: "Objection là chỗ mất đơn nhiều nhất.",
      alternatives: uniqObjections.slice(0, 2).map((o) => o.tip),
    },
    {
      stage: "close",
      goal: "Chốt số lượng / địa chỉ / xác nhận nhận hàng",
      modelLine:
        closeAttempts[0]?.line ||
        "Vậy em ghi [SL] [SP], gửi về [địa chỉ], [N] ngày nữa nhận hàng giúp em nhé?",
      why: "Close cụ thể biến đồng ý thành đơn.",
      alternatives: [
        "Anh/chị lấy 1 hay 2 hộp để nhận đúng ưu đãi hôm nay ạ?",
      ],
    },
  ];

  const whatWorked: string[] = [];
  const whatFailed: string[] = [];
  if (opening.score >= 70) {
    whatWorked.push("Opening có đủ chào / xin phép / intro.");
  } else {
    whatFailed.push("Opening thiếu chào, xin phép hoặc value hook.");
  }
  if (uniqObjections.some((o) => o.handledWell)) {
    whatWorked.push("Có xử lý từ chối đúng hướng.");
  }
  if (uniqObjections.some((o) => !o.handledWell)) {
    whatFailed.push("Có từ chối chưa được xử lý tốt.");
  }
  if (closeAttempts.length) whatWorked.push("Có ít nhất một nỗ lực chốt.");
  else whatFailed.push("Thiếu câu chốt rõ.");
  if (buyingSignals.length) {
    whatWorked.push("Xuất hiện tín hiệu đồng ý/nhận hàng.");
  }
  if (outcome === "won") {
    whatWorked.push("Cuộc gọi kết thúc ở trạng thái chốt được.");
  }
  if (outcome === "lost") {
    whatFailed.push("Cuộc gọi mất đơn — cần siết close + objection.");
  }

  const handled = uniqObjections.filter((o) => o.handledWell).length;
  const objectionScore =
    uniqObjections.length === 0
      ? 65
      : clamp((handled / uniqObjections.length) * 100);

  // Recreation readiness: favor won + high QA score + usable transcript structure.
  // Short won calls with grade A should still clear the 70 threshold.
  const structureScore =
    timeline.length >= 8 ? 85 : timeline.length >= 4 ? 70 : timeline.length >= 2 ? 55 : 35;
  const outcomeBoost =
    outcome === "won" ? 18 : outcome === "callback" ? 6 : outcome === "lost" ? -8 : 0;
  const gradeBoost =
    (input.grade || "").toUpperCase() === "A"
      ? 10
      : (input.grade || "").toUpperCase() === "B"
        ? 4
        : 0;
  const completeBoost = input.isComplete ? 8 : 0;
  const baseReadinessScore = clamp(
    opening.score * 0.18 +
      objectionScore * 0.18 +
      (closeAttempts.length ? 80 : 40) * 0.18 +
      (input.overallScore ?? 60) * 0.22 +
      structureScore * 0.24,
  );
  const readinessScore = clamp(
    baseReadinessScore + outcomeBoost + gradeBoost + completeBoost,
  );


  const agentTalkRatio = clamp((agentChars / totalChars) * 100) / 100;
  const ck = scoreChotKiemTranscript(full);
  const stageScore = (key: StageKey) =>
    stages.find((s) => s.key === key)?.score ?? 0;

  const qualityMetrics: QualityMetric[] = [
    ...ck.criteria.map((c) => ({
      key: `ck_${c.key}`,
      label: c.label,
      score: c.passed ? 100 : 0,
      passed: c.passed,
      weight: c.required ? 1.2 : 0.8,
      evidence: c.evidence || c.value,
      tip: c.passed
        ? "Đạt tiêu chí ChốtKiểm."
        : `Bổ sung: ${c.label.replace(/^\d+\.\s*/, "")}.`,
    })),
    {
      key: "opening_quality",
      label: "Chất lượng mở đầu",
      score: opening.score,
      passed: opening.score >= 70,
      weight: 1,
      evidence: [opening.greeting, opening.permissionAsk, opening.valueHook]
        .filter(Boolean)
        .join(" | "),
      tip: "Chào + xin phép + hook giá trị trong 20 giây đầu.",
    },
    {
      key: "discovery_quality",
      label: "Khai thác nhu cầu",
      score: clamp(
        discoveryQuestions.length * 25 + (stageScore("discovery") > 0 ? 20 : 0),
      ),
      passed: discoveryQuestions.length >= 2,
      weight: 1,
      evidence: discoveryQuestions.slice(0, 2).join(" | "),
      tip: "Hỏi ít nhất 2 câu discovery trước khi pitch.",
    },
    {
      key: "pitch_quality",
      label: "Pitch giá trị",
      score: clamp(pitchPoints.length * 20 + stageScore("pitch") * 0.4),
      passed: pitchPoints.length >= 2,
      weight: 1,
      evidence: pitchPoints.slice(0, 2).join(" | "),
      tip: "Pitch = 1 lợi ích + 1 bằng chứng + 1 ưu đãi.",
    },
    {
      key: "objection_handling",
      label: "Xử lý từ chối",
      score: objectionScore,
      passed: objectionScore >= 65,
      weight: 1.1,
      evidence: uniqObjections
        .slice(0, 2)
        .map((o) => `${o.type}: ${o.handledWell ? "OK" : "Yếu"}`)
        .join(" | "),
      tip: "Xác nhận cảm xúc → bằng chứng → hỏi chốt mềm.",
    },
    {
      key: "close_quality",
      label: "Chốt đơn",
      score: clamp(
        (closeAttempts.length ? 55 : 20) +
          (buyingSignals.length ? 20 : 0) +
          (outcome === "won" ? 25 : 0),
      ),
      passed: closeAttempts.length > 0,
      weight: 1.2,
      evidence: closeAttempts[0]?.line || "",
      tip: "Chốt cụ thể: số lượng + địa chỉ + xác nhận nhận hàng.",
    },
    {
      key: "talk_balance",
      label: "Cân bằng nói chuyện",
      score: clamp(100 - Math.abs(50 - agentTalkRatio * 100) * 1.6),
      passed: agentTalkRatio >= 0.4 && agentTalkRatio <= 0.7,
      weight: 0.7,
      evidence: `TVV ${Math.round(agentTalkRatio * 100)}% / KH ${Math.round((1 - agentTalkRatio) * 100)}%`,
      tip: "Giữ talk-ratio TVV khoảng 45–65%.",
    },
    {
      key: "politeness",
      label: "Lịch sự / chuyên nghiệp",
      score: politenessScore,
      passed: politenessScore >= 70,
      weight: 0.8,
      evidence: toneLabel,
      tip: "Dùng dạ/ạ, tránh áp lực hoặc xen ngang.",
    },
    {
      key: "empathy",
      label: "Đồng cảm",
      score: empathyScore,
      passed: empathyScore >= 60,
      weight: 0.8,
      evidence: "",
      tip: "Phản hồi cảm xúc khách trước khi bán tiếp.",
    },
  ];

  const weightSum = qualityMetrics.reduce((s, m) => s + m.weight, 0) || 1;
  const qualityOverall = clamp(
    qualityMetrics.reduce((s, m) => s + m.score * m.weight, 0) / weightSum,
  );
  const qualityGrade: QualityScorecard["grade"] =
    qualityOverall >= 85 ? "A" : qualityOverall >= 70 ? "B" : qualityOverall >= 55 ? "C" : "D";
  const requiredFailed = qualityMetrics
    .filter((m) => m.key.startsWith("ck_") && !m.passed && m.weight >= 1.2)
    .map((m) => m.label);
  const qualityScorecard: QualityScorecard = {
    overallScore: qualityOverall,
    passRate: clamp(
      (qualityMetrics.filter((m) => m.passed).length / qualityMetrics.length) * 100,
    ),
    grade: qualityGrade,
    chotKiemComplete: ck.complete,
    closeOutcome: ck.closeOutcome,
    closeOutcomeLabel: ck.closeOutcomeLabel,
    metrics: qualityMetrics,
    requiredFailed,
    strengths: qualityMetrics
      .filter((m) => m.passed && m.score >= 70)
      .slice(0, 5)
      .map((m) => m.label),
    gaps: qualityMetrics
      .filter((m) => !m.passed)
      .slice(0, 6)
      .map((m) => m.label),
  };

  const slots: AiCloneSlot[] = [
    {
      key: "agent_name",
      label: "Tên sale",
      value: null,
      required: true,
      example: "Lan",
    },
    {
      key: "company",
      label: "Công ty / thương hiệu",
      value: opening.companyIntro,
      required: true,
      example: "Bảo Việt",
    },
    {
      key: "customer_name",
      label: "Tên khách",
      value: extracted.customerName,
      required: false,
      example: "anh Minh",
    },
    {
      key: "product",
      label: "Sản phẩm",
      value: extracted.product,
      required: true,
      example: "gói bảo hiểm sức khỏe",
    },
    {
      key: "quantity",
      label: "Số lượng",
      value: extracted.quantity,
      required: true,
      example: "1 hộp",
    },
    {
      key: "price",
      label: "Giá",
      value: extracted.price,
      required: true,
      example: "299k",
    },
    {
      key: "offer",
      label: "Ưu đãi",
      value: opening.valueHook || pitchPoints.find((p) => /ưu đãi|tặng|giảm/i.test(p)) || null,
      required: false,
      example: "tặng kèm khám miễn phí",
    },
    {
      key: "address",
      label: "Địa chỉ giao",
      value: extracted.address,
      required: true,
      example: "xã A, huyện B",
    },
    {
      key: "delivery",
      label: "Cam kết giao",
      value: extracted.deliveryPromise,
      required: false,
      example: "2-3 ngày nhận hàng",
    },
    {
      key: "phone",
      label: "SĐT xác nhận",
      value: extracted.phoneMention,
      required: false,
      example: "09xx...",
    },
  ];

  const filledRequired = slots.filter((s) => s.required && s.value).length;
  const requiredSlots = slots.filter((s) => s.required).length || 1;
  const cloneScore = clamp(
    readinessScore * 0.45 +
      qualityOverall * 0.35 +
      (filledRequired / requiredSlots) * 100 * 0.2,
  );
  const aiClonePack: AiClonePack = {
    cloneReady: cloneScore >= 70 && outcome !== "lost",
    cloneScore,
    persona: {
      tone: toneLabel,
      paceWpm: speakingRateWpm,
      politeness: politenessScore,
      empathy: empathyScore,
      assertiveness: confidenceScore,
      styleNotes: toneNotes,
    },
    slots,
    mustSay: [
      opening.greeting || "Em chào anh/chị, em [agent_name] bên [company].",
      opening.permissionAsk || "Anh/chị đang tiện nghe 20–30 giây không ạ?",
      extracted.product
        ? `Em xin giới thiệu [product]${extracted.price ? ", giá [price]" : ""}.`
        : "Em xin giới thiệu [product], giá [price].",
      closeAttempts[0]?.line ||
        "Vậy em ghi [quantity] [product], gửi về [address] giúp anh/chị nhé?",
    ].filter(Boolean),
    avoidSay: [
      ...(riskSignals.length ? ["Tránh lặp lại các câu tạo kháng cự đã xuất hiện."] : []),
      "Không chốt khi chưa xác nhận địa chỉ / số lượng.",
      "Không đả kích đối thủ hoặc ép giá.",
      "Không nói quá nhanh khi nêu giá.",
    ],
    variableScript: recreationScript.map((step) => ({
      stage: step.stage,
      goal: step.goal,
      template: step.modelLine
        .replace(extracted.product || "___", "[product]")
        .replace(extracted.quantity || "___", "[quantity]")
        .replace(extracted.price || "___", "[price]")
        .replace(extracted.address || "___", "[address]")
        .replace(extracted.customerName || "___", "[customer_name]"),
      fillHints: slots
        .filter((s) => step.modelLine.toLowerCase().includes((s.value || "").toLowerCase()) && s.value)
        .map((s) => s.key)
        .slice(0, 4),
    })),
    objectionBranches: uniqObjections.slice(0, 6).map((o) => ({
      trigger: o.type,
      customerLine: o.customerLine,
      reply:
        o.agentReply ||
        "Em hiểu anh/chị đang cân nhắc. Nếu em làm rõ điểm này, anh/chị sẵn sàng thử hôm nay không ạ?",
      tip: o.tip,
    })),
    openers: [
      opening.greeting,
      opening.permissionAsk,
      recreationScript[0]?.alternatives[0] || null,
    ].filter((x): x is string => Boolean(x)),
    closers: [
      ...closeAttempts.slice(0, 3).map((c) => c.line),
      "Anh/chị lấy 1 hay 2 để nhận đúng ưu đãi hôm nay ạ?",
    ].filter(Boolean),
    targetCustomerProfile:
      [
        extracted.customerName ? `KH: ${extracted.customerName}` : null,
        extracted.product ? `Quan tâm: ${extracted.product}` : null,
        outcome === "won" ? "Đã từng đồng ý nhận hàng" : "Cần nurture thêm",
        uniqObjections[0] ? `Hay phản đối: ${uniqObjections[0].type}` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "Khách telesale phổ thông — cần cá nhân hóa slot.",
    recreationNotes: [
      `Clone score ${cloneScore}/100 · QA ${qualityOverall}/100 · Readiness ${readinessScore}/100`,
      ck.summary,
      requiredFailed.length
        ? `Thiếu tiêu chí bắt buộc: ${requiredFailed.join(", ")}`
        : "Đủ tiêu chí bắt buộc ChốtKiểm hoặc không áp dụng đủ.",
      "Điền slot [product]/[price]/[address]/[customer_name] trước khi TTS/AI gọi lại.",
    ],
  };

  const mergedChotKiem = {
    grade: input.grade || qualityGrade,
    overallScore: input.overallScore ?? qualityOverall,
    isComplete: input.isComplete ?? ck.complete,
    criteria: ck.criteria.map((c) => ({
      key: c.key,
      label: c.label,
      passed: c.passed,
      required: c.required,
      value: c.value,
      evidence: c.evidence,
    })),
  };

  return {
    version: ANALYSIS_VERSION,
    analyzedAt: new Date().toISOString(),
    durationSec,
    outcome,
    readinessScore,
    summary: {
      headline:
        input.callSummary?.split("·")[0]?.trim() ||
        (outcome === "won"
          ? "Cuộc gọi có tín hiệu chốt — có thể dùng làm mẫu recreation."
          : "Cuộc gọi cần siết opening/objection/close trước khi dùng làm mẫu."),
      whatWorked: whatWorked.length ? whatWorked : ["Chưa nổi bật điểm mạnh rõ."],
      whatFailed: whatFailed.length
        ? whatFailed
        : ["Chưa phát hiện lỗ hổng lớn từ transcript."],
      oneThingToFix:
        whatFailed[0] ||
        "A/B test thêm 1 biến thể câu chốt xác nhận số lượng + địa chỉ.",
    },
    speakers: {
      agentTalkRatio: clamp((agentChars / totalChars) * 100) / 100,
      customerTalkRatio: clamp((customerChars / totalChars) * 100) / 100,
      turnCount: timeline.length,
      agentTurns: agentTurns.length,
      customerTurns: customerTurns.length,
      avgAgentTurnChars: agentTurns.length
        ? Math.round(agentChars / agentTurns.length)
        : 0,
      avgCustomerTurnChars: customerTurns.length
        ? Math.round(customerChars / customerTurns.length)
        : 0,
    },
    delivery: {
      speakingRateWpm,
      pauseRatio,
      politenessScore,
      empathyScore,
      urgencyScore,
      confidenceScore,
      toneLabel,
      toneNotes,
    },
    timeline,
    stages,
    opening,
    discoveryQuestions,
    pitchPoints,
    objections: uniqObjections,
    closeAttempts,
    buyingSignals,
    riskSignals,
    persuasionTechniques,
    extracted,
    recreationScript,
    qualityScorecard,
    aiClonePack,
    chotKiem: {
      ...mergedChotKiem,
      ...(input.scorecard || {}),
      criteria: mergedChotKiem.criteria,
    },
  };
}

