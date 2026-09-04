/**
 * Deep telesales-call analysis for coaching and later call recreation.
 * Input: transcript (+ optional ChốtKiểm scorecard / summary).
 */

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

export type DeepCallAnalysis = {
  version: 1;
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
  const readinessScore = clamp(
    opening.score * 0.2 +
      objectionScore * 0.2 +
      (closeAttempts.length ? 75 : 35) * 0.2 +
      (input.overallScore ?? 60) * 0.2 +
      (timeline.length >= 6 ? 80 : 40) * 0.2,
  );

  return {
    version: 1,
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
    chotKiem:
      input.scorecard || {
        grade: input.grade,
        overallScore: input.overallScore,
        isComplete: input.isComplete,
      },
  };
}
