import { scoreChotKiemTranscript, type ChotKiemScorecard } from "./chotKiemCriteria";

/**
 * Vietnamese telesales call analyzer.
 * Works on transcripts; audio metrics estimated from text + duration.
 */

export type CallOutcome = "won" | "lost" | "callback" | "unknown";

export type ObjectionFinding = {
  type: string;
  customerLine: string;
  agentReply: string;
  handledWell: boolean;
  tip: string;
};

export type CallAnalysis = {
  openingScore: number;
  openingSummary: string;
  openingSnippet: string;
  objections: ObjectionFinding[];
  closingScore: number;
  closingSummary: string;
  closingSnippet: string;
  speakingRateWpm: number;
  pauseRatio: number;
  toneLabel: string;
  toneNotes: string;
  keywordsHit: string[];
  agreementSignals: string[];
  coachingTips: string[];
  overallScore: number;
  stages: {
    opening: string;
    discovery: string;
    pitch: string;
    objection: string;
    close: string;
  };
  /** Rubric ChốtKiểm — 9 tiêu chí QA (6 bắt buộc) */
  chotKiem: ChotKiemScorecard;
};

export type IndustryPlaybook = {
  industry: string;
  title: string;
  openingScripts: string[];
  objectionScripts: Array<{ objection: string; reply: string }>;
  closingScripts: string[];
  powerKeywords: string[];
  toneGuide: string;
  paceGuide: string;
  winRateHint: string;
};

const POWER_KEYWORDS = [
  "ưu đãi",
  "miễn phí",
  "bảo hành",
  "tiết kiệm",
  "chỉ hôm nay",
  "giảm giá",
  "quà tặng",
  "cam kết",
  "hỗ trợ",
  "lắp đặt",
  "dùng thử",
  "hoàn tiền",
  "trả góp",
  "chính hãng",
  "học thử",
];

const AGREEMENT_SIGNALS = [
  "được",
  "ok",
  "đồng ý",
  "chốt",
  "đăng ký",
  "lấy luôn",
  "gửi link",
  "chuyển khoản",
  "ok em",
  "vâng",
  "gửi đi",
];

const OPENING_GOOD = [
  "em chào",
  "tiện nói",
  "em gọi",
  "em xin",
  "30 giây",
  "20 giây",
  "không ạ",
  "bên ",
];

const OBJECTION_PATTERNS: Array<{
  type: string;
  patterns: RegExp[];
  tip: string;
  goodReplies: RegExp[];
}> = [
  {
    type: "Giá cao / không có tiền",
    patterns: [/đắt/i, /không có tiền/i, /mắc/i, /giá cao/i],
    tip: "Tách giá thành chi phí/ngày, nhấn ROI hoặc ưu đãi có hạn, hỏi ngân sách.",
    goodReplies: [/ưu đãi/i, /tiết kiệm/i, /trả góp/i, /ngân sách/i, /nghìn/i],
  },
  {
    type: "Đang dùng đối thủ",
    patterns: [/đang dùng/i, /đã có/i, /xài bên/i, /của công ty/i],
    tip: "Không tấn công đối thủ — hỏi điểm chưa hài lòng rồi đề xuất khác biệt.",
    goodReplies: [/đối chiếu/i, /điểm trống/i, /khác/i, /hài lòng/i, /trần/i],
  },
  {
    type: "Để suy nghĩ",
    patterns: [/suy nghĩ/i, /để xem/i, /cân nhắc/i, /mai nói/i],
    tip: "Hỏi lo ngại cụ thể + nhắc ưu đãi hôm nay + chốt lịch gọi lại.",
    goodReplies: [/lo/i, /hôm nay/i, /gọi lại/i, /giờ nào/i, /ưu đãi/i],
  },
  {
    type: "Không quan tâm / đang bận",
    patterns: [/không quan tâm/i, /đang bận/i, /không cần/i, /thôi em/i],
    tip: "Xin 20 giây value prop; nếu vẫn bận thì hẹn lại — đừng pitch dài.",
    goodReplies: [/giây/i, /ngắn/i, /hẹn/i, /khi nào/i],
  },
  {
    type: "Nghi ngờ / sợ lừa / sợ không hiệu quả",
    patterns: [/lừa/i, /uy tín/i, /có thật/i, /không hiệu quả/i, /sợ/i],
    tip: "Đưa bằng chứng: bảo hành, hoàn tiền, hợp đồng, số khách.",
    goodReplies: [/bảo hành/i, /hoàn tiền/i, /cam kết/i, /hợp đồng/i, /khách/i],
  },
];

const CLOSE_PATTERNS = [
  /đăng ký/i,
  /chốt/i,
  /em gửi link/i,
  /thanh toán/i,
  /giữ chỗ/i,
  /giữ suất/i,
  /tạo đơn/i,
  /xác nhận/i,
];

function splitLines(transcript: string): string[] {
  return transcript
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

function isCustomer(line: string): boolean {
  return /^(khách|customer|khách hàng|kh)\s*[:\-]/i.test(line);
}

function isAgent(line: string): boolean {
  return /^(sale|agent|tvv|telesale|em)\s*[:\-]/i.test(line);
}

function stripSpeaker(line: string): string {
  return line.replace(
    /^(khách|customer|khách hàng|kh|sale|agent|tvv|telesale|em)\s*[:\-]\s*/i,
    "",
  );
}

function countWords(text: string): number {
  return text.replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean).length;
}

function clamp(n: number, min = 0, max = 100): number {
  return Math.max(min, Math.min(max, Math.round(n)));
}

function findKeywords(text: string): string[] {
  const lower = text.toLowerCase();
  return POWER_KEYWORDS.filter((k) => lower.includes(k));
}

function findAgreement(text: string): string[] {
  const lower = text.toLowerCase();
  return AGREEMENT_SIGNALS.filter((k) => lower.includes(k));
}

export function analyzeCallTranscript(input: {
  transcript: string;
  durationSec: number;
  outcome?: CallOutcome;
}): CallAnalysis {
  const lines = splitLines(input.transcript);
  const full = input.transcript;
  const openingBlock = lines.slice(0, Math.min(8, lines.length)).join("\n");
  const closingBlock = lines.slice(Math.max(0, lines.length - 8)).join("\n");
  const openingText = openingBlock.toLowerCase();

  let openingScore = 40;
  const openingHits = OPENING_GOOD.filter((p) => openingText.includes(p));
  openingScore += openingHits.length * 8;
  if (/em chào anh|em chào chị|em chào anh chị|em chào bạn/i.test(openingBlock)) {
    openingScore += 8;
  }
  if (/em gọi|bên /i.test(openingBlock)) openingScore += 8;
  if (/bận|tiện|giây/i.test(openingBlock)) openingScore += 10;
  if (openingBlock.length < 40) openingScore -= 15;
  openingScore = clamp(openingScore);

  const openingSummary =
    openingHits.length >= 2
      ? "Opening có chào hỏi + xin phép/giới thiệu — đúng khung telesale chuẩn."
      : openingHits.length === 1
        ? "Opening còn thiếu 1 yếu tố: chào hỏi, xin phép thời gian, hoặc value prop ngắn."
        : "Opening yếu — nên chào rõ danh tính, xin 30 giây, nêu lợi ích ngay.";

  const objections: ObjectionFinding[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const content = stripSpeaker(line);
    const objectionLike =
      isCustomer(line) ||
      /đắt|bận|sợ|suy nghĩ|đang dùng|không quan tâm|lừa|hiệu quả|giá cao/i.test(content);
    if (!objectionLike) continue;

    for (const pattern of OBJECTION_PATTERNS) {
      if (!pattern.patterns.some((re) => re.test(content))) continue;
      const replyLine =
        lines.slice(i + 1, i + 5).find((l) => isAgent(l) || /^sale\b/i.test(l)) ?? "";
      const reply = stripSpeaker(replyLine);
      const handledWell =
        reply.length > 12 && pattern.goodReplies.some((re) => re.test(reply));
      objections.push({
        type: pattern.type,
        customerLine: content,
        agentReply: reply || "(Không thấy phản hồi rõ trong transcript)",
        handledWell,
        tip: pattern.tip,
      });
    }
  }

  const seen = new Set<string>();
  const uniqueObjections = objections.filter((o) => {
    if (seen.has(o.type)) return false;
    seen.add(o.type);
    return true;
  });

  let closingScore = 35;
  const closeHits = CLOSE_PATTERNS.filter((re) => re.test(closingBlock));
  closingScore += closeHits.length * 10;
  if (/ưu đãi|hôm nay|chỉ còn|giữ/i.test(closingBlock)) closingScore += 10;
  if (/link|thanh toán|đăng ký|chốt|tạo đơn/i.test(closingBlock)) closingScore += 12;
  if (input.outcome === "won") closingScore += 10;
  if (input.outcome === "lost" && closeHits.length === 0) closingScore -= 10;
  closingScore = clamp(closingScore);

  const closingSummary =
    closeHits.length > 0
      ? "Có CTA chốt rõ ràng ở cuối cuộc gọi."
      : "Thiếu câu chốt — nên hỏi chốt mềm + đề xuất bước tiếp theo cụ thể.";

  const words = Math.max(countWords(full), Math.round(full.replace(/\s+/g, "").length / 4));
  const minutes = Math.max(input.durationSec / 60, 0.4);
  let speakingRateWpm = Math.round((words * 0.65) / minutes);
  // Guardrails: telesale speech usually 90–180 WPM
  if (speakingRateWpm < 70) speakingRateWpm = Math.min(150, Math.max(90, Math.round(words / Math.max(minutes * 0.5, 0.3))));
  if (speakingRateWpm > 200) speakingRateWpm = 180;
  const sentenceCount = (full.match(/[.!?…]+/g) ?? []).length || lines.length;
  const pauseRatio = clamp((sentenceCount / Math.max(words, 1)) * 100, 5, 40) / 100;

  let toneLabel = "Trung tính";
  let toneNotes = "Giọng đều, chưa rõ điểm nhấn cảm xúc.";
  if (speakingRateWpm > 170) {
    toneLabel = "Nhanh / áp lực";
    toneNotes = "Tốc độ cao — dễ làm khách phòng thủ. Giảm 10–15% tốc độ khi pitch giá.";
  } else if (speakingRateWpm < 110) {
    toneLabel = "Chậm / thiếu năng lượng";
    toneNotes = "Hơi chậm — tăng năng lượng nhẹ ở opening và closing.";
  } else if (uniqueObjections.some((o) => o.handledWell) || input.outcome === "won") {
    toneLabel = "Tự tin – đồng cảm";
    toneNotes = "Nhịp nói ổn, có dấu hiệu lắng nghe và dẫn dắt.";
  } else if (/ạ|dạ|anh chị/i.test(full)) {
    toneLabel = "Lịch sự – mềm";
    toneNotes = "Lịch sự tốt; cần thêm chắc chắn khi chốt.";
  }

  const keywordsHit = findKeywords(full);
  const agreementSignals = findAgreement(full);

  const coachingTips: string[] = [];
  if (openingScore < 70) {
    coachingTips.push(
      "Opening mẫu: “Em chào anh/chị, em [Tên] bên [Công ty]. Anh/chị đang tiện nói 30 giây không ạ?”",
    );
  }
  if (uniqueObjections.some((o) => !o.handledWell)) {
    coachingTips.push(
      "Khi bị từ chối: xác nhận cảm xúc → hỏi lý do cụ thể → đưa 1 bằng chứng → hỏi chốt mềm.",
    );
  }
  if (closingScore < 70) {
    coachingTips.push(
      "Chốt: “Nếu hợp, em gửi link đăng ký ưu đãi hôm nay luôn nhé — anh/chị dùng luôn được không ạ?”",
    );
  }
  if (speakingRateWpm > 160) {
    coachingTips.push("Giảm tốc độ còn ~130–150 từ/phút; nhấn mạnh từ khóa lợi ích.");
  }
  if (keywordsHit.length < 2) {
    coachingTips.push(
      "Rải 2–3 từ khóa lực: ưu đãi, bảo hành, tiết kiệm, dùng thử — đúng lúc khách do dự.",
    );
  }
  if (coachingTips.length === 0) {
    coachingTips.push("Cuộc gọi khá tốt — giữ nhịp này và A/B test thêm 1 biến thể câu chốt.");
  }

  const handled = uniqueObjections.filter((o) => o.handledWell).length;
  const objectionScore =
    uniqueObjections.length === 0
      ? 70
      : clamp((handled / uniqueObjections.length) * 100);

  const overallScore = clamp(
    openingScore * 0.25 +
      objectionScore * 0.25 +
      closingScore * 0.3 +
      (keywordsHit.length >= 2 ? 80 : 55) * 0.1 +
      (speakingRateWpm >= 120 && speakingRateWpm <= 160 ? 85 : 60) * 0.1,
  );

  return {
    openingScore,
    openingSummary,
    openingSnippet: stripSpeaker(lines[0] ?? openingBlock).slice(0, 220),
    objections: uniqueObjections,
    closingScore,
    closingSummary,
    closingSnippet: stripSpeaker(lines[lines.length - 1] ?? closingBlock).slice(0, 220),
    speakingRateWpm,
    pauseRatio,
    toneLabel,
    toneNotes,
    keywordsHit,
    agreementSignals,
    coachingTips,
    overallScore,
    stages: {
      opening: openingScore >= 70 ? "Tốt" : openingScore >= 50 ? "Ổn" : "Cần cải thiện",
      discovery: /anh chị đang|nhu cầu|đang dùng|quan tâm|hướng tới|ngân sách/i.test(full)
        ? "Có hỏi nhu cầu"
        : "Thiếu discovery",
      pitch: keywordsHit.length ? "Có nêu lợi ích" : "Pitch chung chung",
      objection:
        uniqueObjections.length === 0
          ? "Không gặp từ chối rõ"
          : handled >= uniqueObjections.length / 2
            ? "Xử lý khá"
            : "Xử lý yếu",
      close: closingScore >= 70 ? "Có CTA" : "Thiếu chốt",
    },
    chotKiem: scoreChotKiemTranscript(full),
  };
}

export const INDUSTRY_PLAYBOOKS: IndustryPlaybook[] = [
  {
    industry: "Bảo hiểm",
    title: "Playbook bảo hiểm nhân thọ / sức khỏe",
    openingScripts: [
      "Em chào anh/chị, em [Tên] bên [Công ty bảo hiểm]. Em gọi vì chương trình chăm sóc sức khỏe đang có ưu đãi khám miễn phí — anh/chị tiện nghe 30 giây không ạ?",
      "Em xin phép làm phiền ngắn: nhiều khách cùng độ tuổi anh/chị đang bổ sung gói nằm viện — em gửi thông tin nhanh được không ạ?",
    ],
    objectionScripts: [
      {
        objection: "Đắt quá",
        reply:
          "Em hiểu ạ. Gói này khoảng [X]đ/ngày, chưa bằng một ly trà sữa, mà khi nằm viện được chi trả đến [Y]. Anh/chị đang ưu tiên ngân sách khoảng bao nhiêu ạ?",
      },
      {
        objection: "Đã có bảo hiểm công ty",
        reply:
          "Bảo hiểm công ty thường giới hạn trần và người thân. Em đối chiếu giúp điểm trống — nếu trùng em không tư vấn thêm ạ.",
      },
    ],
    closingScripts: [
      "Em giữ ưu đãi khám miễn phí đến hết hôm nay. Anh/chị cho em mã CCCD để giữ chỗ tư vấn 15 phút được không ạ?",
    ],
    powerKeywords: ["ưu đãi", "bảo vệ", "chi trả", "miễn phí", "cam kết"],
    toneGuide: "Đồng cảm, chậm rãi khi nói rủi ro; chắc chắn khi nêu quyền lợi.",
    paceGuide: "120–140 WPM. Nghỉ 0.5–1s sau khi nêu số tiền.",
    winRateHint: "Opening xin phép + discovery sức khỏe hiện tại làm tăng tỉ lệ hẹn tư vấn.",
  },
  {
    industry: "Thực phẩm chức năng",
    title: "Playbook TPCN / sức khỏe",
    openingScripts: [
      "Em chào anh/chị, em [Tên] từ [Thương hiệu]. Em gọi chia sẻ chương trình dùng thử 7 ngày cho [công dụng] — anh/chị đang quan tâm [vấn đề] không ạ?",
    ],
    objectionScripts: [
      {
        objection: "Sợ không hiệu quả",
        reply:
          "Em ghi nhận ạ. Sản phẩm có hoàn tiền 7 ngày nếu không phù hợp, kèm hướng dẫn dùng. Anh/chị dùng thử trước rủi ro thấp hơn nhiều ạ.",
      },
      {
        objection: "Đang dùng loại khác",
        reply:
          "Anh/chị dùng được bao lâu và cảm nhận thế nào ạ? Nhiều khách kết hợp/chuyển vì [điểm khác biệt] — em gửi so sánh ngắn nhé.",
      },
    ],
    closingScripts: [
      "Hôm nay có quà [X] khi chốt 2 hộp. Em gửi link đặt hàng luôn để giữ quà nhé ạ?",
    ],
    powerKeywords: ["dùng thử", "hoàn tiền", "quà tặng", "liệu trình", "cam kết"],
    toneGuide: "Thân thiện, tránh 'tăng bo'; nhấn trải nghiệm và an toàn.",
    paceGuide: "130–150 WPM. Nhịp vui nhẹ ở phần quà/ưu đãi.",
    winRateHint: "Nhắc hoàn tiền + liệu trình ngắn giúp giảm từ chối 'sợ không hiệu quả'.",
  },
  {
    industry: "Điện máy / gia dụng",
    title: "Playbook điện máy",
    openingScripts: [
      "Em chào anh/chị, em [Tên] siêu thị [Brand]. Máy [SP] đang giảm [X]% + lắp đặt miễn phí khu vực anh/chị — em báo nhanh 20 giây được không ạ?",
    ],
    objectionScripts: [
      {
        objection: "Để suy nghĩ",
        reply:
          "Em hiểu ạ. Ưu đãi còn [N] suất hôm nay. Anh/chị đang do giá hay model ạ? Em chốt đúng nhu cầu để khỏi mất ưu đãi.",
      },
      {
        objection: "Mua online rẻ hơn",
        reply:
          "Em so giúp: bên em gồm VAT + lắp đặt + bảo hành chính hãng tận nơi. Tổng ra thường tối ưu hơn flash sale không kèm dịch vụ ạ.",
      },
    ],
    closingScripts: [
      "Em giữ giá flash + lịch lắp trong tuần này. Anh/chị chốt luôn, em tạo đơn và gửi mã vận đơn nhé?",
    ],
    powerKeywords: ["lắp đặt tận nơi", "bảo hành", "giảm giá", "chính hãng", "miễn phí"],
    toneGuide: "Nhanh gọn, rõ ràng về số liệu; tự tin khi so sánh tổng sở hữu.",
    paceGuide: "140–155 WPM khi báo giá; chậm lại khi nói bảo hành.",
    winRateHint: "So sánh 'giá + lắp + bảo hành' thắng flash sale trần.",
  },
  {
    industry: "Giáo dục / khóa học",
    title: "Playbook giáo dục",
    openingScripts: [
      "Em chào anh/chị, em [Tên] từ [Trung tâm]. Em gọi vì hồ sơ [lộ trình] đang mở suất học thử miễn phí tuần này — anh/chị đang hướng tới [mục tiêu] đúng không ạ?",
    ],
    objectionScripts: [
      {
        objection: "Không có thời gian",
        reply:
          "Lộ trình có ca tối/cuối tuần và bài ~30 phút/ngày. Anh/chị trống khung nào trong tuần để em xếp lớp thử?",
      },
      {
        objection: "Học online sợ không chất lượng",
        reply:
          "Lớp có mentor kèm và cam kết đầu ra. Anh/chị học thử 1 buổi miễn phí rồi quyết định ạ.",
      },
    ],
    closingScripts: [
      "Em giữ suất học thử + ưu đãi học phí đến [deadline]. Anh/chị chọn ca [A/B], em gửi link đăng ký luôn nhé?",
    ],
    powerKeywords: ["học thử", "cam kết", "ưu đãi", "mentor", "đầu ra"],
    toneGuide: "Truyền cảm hứng vừa phải; hỏi mục tiêu trước khi pitch.",
    paceGuide: "125–145 WPM. Dừng sau khi hỏi mục tiêu.",
    winRateHint: "Discovery mục tiêu trước pitch làm tăng tỉ lệ học thử.",
  },
  {
    industry: "Bất động sản",
    title: "Playbook BĐS / căn hộ",
    openingScripts: [
      "Em chào anh/chị, em [Tên] sàn [Brand]. Em có suất căn [loại] view [X] đang giữ chỗ — anh/chị đang tìm ở hay đầu tư ạ?",
    ],
    objectionScripts: [
      {
        objection: "Giá cao",
        reply:
          "Em tách tiến độ thanh toán và hỗ trợ vay. Với ngân sách anh/chị, em lọc căn phù hợp hơn — anh/chị quanh mức nào ạ?",
      },
      {
        objection: "Sợ pháp lý",
        reply:
          "Dự án có [sổ/GPXD]. Em gửi hồ sơ pháp lý + lịch xem nhà thực tế để anh/chị kiểm chứng trực tiếp ạ.",
      },
    ],
    closingScripts: [
      "Em giữ suất tham quan + ưu đãi phí quản lý trong tuần. Anh/chị chọn sáng/chiều cuối tuần, em sắp lịch luôn nhé?",
    ],
    powerKeywords: ["giữ chỗ", "pháp lý", "hỗ trợ vay", "ưu đãi", "tiến độ"],
    toneGuide: "Chuyên nghiệp, không hối thúc quá; dẫn bằng lịch xem thực tế.",
    paceGuide: "120–140 WPM. Nhấn mạnh pháp lý rõ ràng.",
    winRateHint: "Chốt lịch xem nhà thắng hơn chốt tiền ngay trên call.",
  },
];

export function buildTeamInsights(
  calls: Array<{
    industry: string;
    outcome: CallOutcome;
    analysis: CallAnalysis;
  }>,
) {
  const total = calls.length || 1;
  const won = calls.filter((c) => c.outcome === "won").length;
  const avg = (getter: (a: CallAnalysis) => number) =>
    Math.round(calls.reduce((s, c) => s + getter(c.analysis), 0) / total);

  const keywordStats = new Map<string, { won: number; total: number }>();
  for (const call of calls) {
    for (const kw of call.analysis.keywordsHit) {
      const cur = keywordStats.get(kw) ?? { won: 0, total: 0 };
      cur.total += 1;
      if (call.outcome === "won") cur.won += 1;
      keywordStats.set(kw, cur);
    }
  }

  const topKeywords = [...keywordStats.entries()]
    .map(([keyword, s]) => ({
      keyword,
      lift: s.total ? Math.round((s.won / s.total) * 100) : 0,
      count: s.total,
    }))
    .sort((a, b) => b.lift - a.lift || b.count - a.count)
    .slice(0, 8);

  const bestOpenings = calls
    .filter((c) => c.outcome === "won")
    .sort((a, b) => b.analysis.openingScore - a.analysis.openingScore)
    .slice(0, 5)
    .map((c) => c.analysis.openingSnippet);

  const bestCloses = calls
    .filter((c) => c.outcome === "won")
    .sort((a, b) => b.analysis.closingScore - a.analysis.closingScore)
    .slice(0, 5)
    .map((c) => c.analysis.closingSnippet);

  const objectionTips = calls
    .flatMap((c) => c.analysis.objections)
    .filter((o) => o.handledWell)
    .slice(0, 8);

  const byIndustry = new Map<string, { won: number; total: number; avgScore: number }>();
  for (const call of calls) {
    const cur = byIndustry.get(call.industry) ?? { won: 0, total: 0, avgScore: 0 };
    cur.total += 1;
    cur.avgScore += call.analysis.overallScore;
    if (call.outcome === "won") cur.won += 1;
    byIndustry.set(call.industry, cur);
  }

  return {
    winRate: Math.round((won / total) * 100),
    avgOpening: avg((a) => a.openingScore),
    avgClosing: avg((a) => a.closingScore),
    avgOverall: avg((a) => a.overallScore),
    avgWpm: avg((a) => a.speakingRateWpm),
    topKeywords,
    bestOpenings,
    bestCloses,
    objectionTips,
    industries: [...byIndustry.entries()].map(([industry, s]) => ({
      industry,
      winRate: Math.round((s.won / s.total) * 100),
      avgScore: Math.round(s.avgScore / s.total),
      total: s.total,
    })),
    criteriaPassRate: (() => {
      const cards = calls.map((c) => c.analysis.chotKiem);
      if (!cards.length) return 0;
      return Math.round(cards.reduce((s, c) => s + c.passRate, 0) / cards.length);
    })(),
    criteriaCompleteRate: (() => {
      const cards = calls.map((c) => c.analysis.chotKiem);
      if (!cards.length) return 0;
      return Math.round((cards.filter((c) => c.complete).length / cards.length) * 100);
    })(),
    criteriaGaps: (() => {
      const gap = new Map<string, number>();
      for (const call of calls) {
        for (const c of call.analysis.chotKiem.criteria) {
          if (c.required && !c.passed) {
            gap.set(c.shortLabel, (gap.get(c.shortLabel) ?? 0) + 1);
          }
        }
      }
      return [...gap.entries()]
        .map(([label, count]) => ({ label, count }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 6);
    })(),
    realismNote:
      "Phân tích transcript hiện đạt ~75–85% độ gần coach người. Ngữ điệu từ text là ước lượng; gắn file audio + model prosody để nâng lên ~85–90%.",
  };
}
