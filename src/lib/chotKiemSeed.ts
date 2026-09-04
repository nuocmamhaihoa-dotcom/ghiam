import type { CallOutcome } from "./analyzeCall";

/** Snapshot mẫu lấy từ ChốtKiểm (SĐT đã mask). Không chứa credential. */
export type LiveSeedCall = {
  id: string;
  title: string;
  industry: string;
  product: string;
  agentName: string;
  outcome: CallOutcome;
  durationSec: number;
  transcript: string;
  source: "chotkiem";
  externalId: string;
  phoneMasked: string;
  liveGrade: string;
  liveScore: number;
  liveSummary: string;
};

/** Snapshot từ /api/dashboard + /api/calls/criteria-report (fallback khi API offline). */
export const CHOTKIEM_LIVE_STATS = {
  calls: 709,
  avgScore: 65.8,
  completeCalls: 279,
  uniquePhones: 657,
  agents: 36,
  passRate: 39,
  topGaps: [
    { key: "greeting", label: "1. Chào khách hàng", failCount: 52 },
    { key: "price", label: "4. Chốt giá bán sản phẩm", failCount: 21 },
    { key: "productName", label: "2. Giới thiệu tên sản phẩm", failCount: 20 },
    { key: "quantity", label: "3. Chốt số lượng sản phẩm", failCount: 20 },
    { key: "deliveryAddress", label: "5. Chốt rõ địa chỉ khách hàng", failCount: 18 },
    { key: "customerAgreed", label: "6. Khách hàng đồng ý nhận hàng", failCount: 15 },
  ],
  fetchedAt: "2026-09-04",
  baseUrl: "http://222.255.215.55",
} as const;

export const CHOTKIEM_SEED_CALLS: LiveSeedCall[] = [
  {
    id: "ck_1093e620",
    title: "ChốtKiểm · C89394-QUYEN HAI MINH · A",
    industry: "Chốt đơn (live)",
    product: "Bánh chối gì",
    agentName: "C89394-QUYEN HAI MINH",
    outcome: "won",
    durationSec: 103,
    transcript: `Vâng em chào anh ạ kem sẻ chị ạ trưa nhà mình có đơn hàng là hai cái hộp bột điều hòa axit cho cây được tặng thêm một gói kích hoa chừng chín nghìn đi à. Mình về 99k cũ là xã nào á á ạ dạ 99k xã chị đọc lại chối em cái địa chỉ 99k đọc lại trên dạ dạ bảy bảy trăm bao nhiêu ạ bảy trăm bao nhiêu chị? Nhà mình có đơn hàng là hai 99k hộp bột điều hòa axit cho de được tặng thêm một gói kích hoa là chín mươi chín nghìn đi ạ mình gửi về đâu từ. Bánh chối chi. Cái bột kích hoa 99k axit thì chị. Mình ở xã nào ạ? Mình ở xã phường nào chị? Ở xã cai Lạc. 99k ạ. Huyện ạ. Huyện ạ. Huyện từ de chút này mi đọc lại xem cái tên xã nhé Nam chủ á Không chị Hoa Hoa Nguyễn đây à đúng chưa ạ Vâng tổng đất hàng nhà mình là hai hộp được tặng thêm một gói kích hoa là chín mươi chín nghìn miễn ship hai ngày nữa nhận hàng nhớ kiểm tra thôi em nhé`,
    source: "chotkiem",
    externalId: "1093e620-e996-4e6d-95aa-15d181107ef4",
    phoneMasked: "035***242",
    liveGrade: "A",
    liveScore: 100,
    liveSummary: "SP: Bánh chối gì · SL: 2 hộp · Giá: 99.000đ · Địa chỉ: hoa chung · Chưa đồng ý nhận hàng · Cảm xúc: neutral",
  },
  {
    id: "ck_3df11ad1",
    title: "ChốtKiểm · Linh 2K · A",
    industry: "Chốt đơn (live)",
    product: "Collagen huyết thanh về Huế đây à",
    agentName: "Linh 2K",
    outcome: "won",
    durationSec: 82,
    transcript: `Chị ơi, nhà mình có một cái hộp Gel Collagen huyết thanh 99.000 hôm qua ấy à. Nhận hàng về xa cũ nhà mình đo xã gì trước khi sáng nhập đây ạ? Không đồng ý Mình nhận hàng hôm qua cái lọ Gel Collagen huyết thanh về Huế đây à? Phường cũ nhà mình nhận hàng trước khi xác nhập đó là phường gì đấy chị? Phường cũ đó là phường gì ạ? Phường gì ạ? Phường cũ đó. Phường gì, phường cũ nhà mình ấy, phường gì trước khi xác nhập đấy? Phường Hương Sơ. À, phường Hương Sơ… Hương Sơ ạ? Phường Hương Sơ, thành phố Huế, hai hôm nữa nhận hàng giúp em nhé, một hộp 99 nghìn chị nhé. Chị đặt là 2 hộp. À 2 hộp là 150.000 chị nhé, 2 hộp. 2 hộp nữa có hàng nhé. Vâng, em cảm ơn chị.`,
    source: "chotkiem",
    externalId: "3df11ad1-6e4c-4b49-9030-97f240d146b1",
    phoneMasked: "093***296",
    liveGrade: "A",
    liveScore: 100,
    liveSummary: "SP: Collagen huyết thanh về Huế đây à · SL: 2 hộp · Giá: 99.000đ · Địa chỉ: thanh · Chưa đồng ý nhận hàng · Cảm xúc: positive",
  },
  {
    id: "ck_e9fcadb5",
    title: "ChốtKiểm · Lan · A",
    industry: "Chốt đơn (live)",
    product: "huyết thanh collagen kem hỏi",
    agentName: "Lan",
    outcome: "won",
    durationSec: 188,
    transcript: `Nhân viên: Chị ơi Kh có đơn hàng lúc sáng đặt bên em cái Showroom cá huyết thanh collagen kem hỏi lại cái phường cũ ấy, từ phường cũ là phường nào chị
Khách: nhỉ?
Khách: Phường kh là vườn
Khách: Từ Bình Thuận Không De
Khách: lấy luôn một combo ngày đêm nha hai hộp một trăm năm mươi nghìn
Khách: không
Nhân viên: không bảo đêm đang mở mở kh lấy lấy có một hộp đúng không thì em bảo là sao chị không lấy một không? Có kh cố đêm đâu Chị bên nhà thuốc Minh Anh lúc sáng nay mới chốt de chị kem thấy chị lấy có một hộp với
Khách: gel là chị lấy
Khách: kh cô ra gen bằng riêng chứ không phải hộp. Viên và showroom
Nhân viên: một hộp mà bao nhiêu viên mà chị viên viên con cá huyết thanh đấy chị có sáng nay chị mới đặt mà đấy là sơ đôn viên mà thì em lại gọi là hộp thì em mới bảo là mọi chị lấy kh ngày đêm với ạ lấy hộp là một trăm năm mươi nghìn đấy ạ.
Khách: Không có cái này nó là có một trăm bốn mươi nghìn này
Nhân viên: lấy hộp
Khách: kh
Nhân viên: kh Vậy thế là bạn ấy bớt cho mình chứ không phải vậy đâu chị ạ. Về làm của mình là bạn ấy bớt là hai hộp một trăm bốn mươi nghìn đúng không chị xoa huyễn hồng hoa không hồng đấy về Bình Thuận bên nhà thuốc Minh Anh vào lúc sáng sớm lên đấy chị
Khách: lên đấy chị. Em
Khách: lấy căn đêm không kem.
Nhân viên: Vâng thì em không thấy bạn de nộp vào đây thì em mới hỏi thôi có bạn ấy chốt rồi thì thôi. Vâng chị ơi về cái này nó nhận hàng cho em chị không. Tổng hai hộp một trăm kh Bình nhìn nha
Khách: de lấy căn đêm một trăm bốn mươi ngàn nha.
Nhân viên: Kh`,
    source: "chotkiem",
    externalId: "e9fcadb5-1f19-4761-aa05-cacac7b1c2d9",
    phoneMasked: "091***176",
    liveGrade: "A",
    liveScore: 100,
    liveSummary: "SP: huyết thanh collagen kem hỏi · SL: 1 hộp · Địa chỉ: binh thuan · Chưa đồng ý nhận hàng · Cảm xúc: neutral",
  },
  {
    id: "ck_bce092f8",
    title: "ChốtKiểm · Vui · F",
    industry: "Chốt đơn (live)",
    product: "cho mình rồi đấy ba ngày nữa nhận hàng giúp em chị nhé De là",
    agentName: "Vui",
    outcome: "lost",
    durationSec: 60,
    transcript: `Alo chị ơi tháng nay em gửi hàng cho mình rồi đấy ba ngày nữa nhận hàng giúp em chị nhé De làm sao de ba gói là sáu mươi nghìn miễn kh cơ mà không yên tâm ba gói hạt giống bí là không mươi nghìn`,
    source: "chotkiem",
    externalId: "bce092f8-52b1-4405-8b29-d5cb1550e17f",
    phoneMasked: "034***854",
    liveGrade: "F",
    liveScore: 67,
    liveSummary: "SP: cho mình rồi đấy ba ngày nữa nhận hàng giúp em chị nhé De làm sao de ba gói · SL: 3 gói · Giá: 60k · Chưa đồng ý nhận hàng · Cảm xúc: neutral",
  },
  {
    id: "ck_1fbaa4c0",
    title: "ChốtKiểm · Hang lớn · F",
    industry: "Chốt đơn (live)",
    product: "gel xoa bóp",
    agentName: "Hang lớn",
    outcome: "lost",
    durationSec: 66,
    transcript: `Nhân viên: Chị buồn nghe kênh ở đây, chứ không rủ họ đi, hãy nhấn nút đăng ký để ủng hộ kênh mình nhé. Chị buồn nghe kênh ở đây, chứ không rủ họ đi, hãy nhấn nút đăng ký để ủng hộ kênh mình nhé.
Khách: Đồng ý mua, chốt đơn giúp em, giao hàng COD.
Nhân viên: Từ chối, trung lập, bình thường, bản ghi không tách được lời khách,
Khách: không lấy đâu, bản ghi không tách được lời khách, không lấy, 2 hộp, gel collagen, gel xoa bóp,
Nhân viên: Viên thảo dược đuổi chuột, kem kh, tinh chất c, gói viên thảo dược đồ ra đơn, hai Tip, cổ họng.`,
    source: "chotkiem",
    externalId: "1fbaa4c0-1848-43e1-b3a8-641cadf7b10b",
    phoneMasked: "079***151",
    liveGrade: "F",
    liveScore: 50,
    liveSummary: "SP: gel xoa bóp · SL: 2 hộp · Chưa đồng ý nhận hàng · Cảm xúc: positive",
  },
  {
    id: "ck_b5fbc149",
    title: "ChốtKiểm · Đào · F",
    industry: "Chốt đơn (live)",
    product: "từ là tốt nhất trên thị trường rồi đấy anh ạ Nó về bón anh n",
    agentName: "Đào",
    outcome: "lost",
    durationSec: 190,
    transcript: `Người nói 0: Làm người đàn ông bản lĩnh Một là phải chìm tĩnh trước đáy bình và không được giật mình trước đáy trấu.
Người nói 0: Kh không được đầu gấu với đáy ngoan mà không cần nhẹ nhàng với cái dữ. Ba. Không được tự tử nếu mất
Người nói 1: Kh phải mua gói bao nhiêu tiền nào
Người nói 2: mười gói là hai trăm ba mươi nghìn miễn phí ship anh ạ
Người nói 0: mười gói hai trăm ba mươi nghìn ạ
Người nói 2: Kh, sản phẩm từ là tốt nhất trên thị trường rồi đấy anh ạ Nó về bón anh nhá
Người nói 1: de
Người nói 2: gói
Người nói 2: không
Người nói 2: Vâng
Người nói 2: kh gói một sào ạ, thế đến năm gói cũng được thế năm gói là một trăm
Người nói 2: ba mươi lăm nghìn anh nhé
Người nói 2: Thế mà nó kh sào xào rưỡi rưỡi bảy sào rưỡi thì anh lấy mười gói đi mỗi gói hơn một sào xem là được anh ạ
Người nói 2: À kh
Người nói 2: vào kh sào hơn một gói tí xem là được anh ạ
Người nói 2: Thế cả mười gói đi cho bảy sào lấy vừa anh ạ
Người nói 1: Vâng nhưng mà để
Người nói 1: chưa lên ngay từ để bị đón đòng mới
Người nói 1: phun được thường?
Người nói 2: Không, phun sớm bây giờ anh cái này gel kích thích cho cây da rễ kh
Người nói 2: anh kh càng sớm càng tốt anh ạ
Người nói 1: Chưa kịp lúa lúa
Người nói 1: cấy được một tháng rồi
Người nói 2: Thoải thái anh ơi, mười năm, hai mươi ngày bắt đầu de dùng rồi anh ạ một tháng là dùng thoải mái rồi anh ạ ạ Kem lấy sao anh trả mười gói đạt hai trăm ba mươi nghìn gửi về
Người nói 2: xã Xã Mộc có huyện Võ Nha Thái Nguyên hai để anh lấy hàng xem rồi nhé
Người nói 2: Vâng em cảm ơn`,
    source: "chotkiem",
    externalId: "b5fbc149-d46d-4d62-96a6-5b0ccd0e0439",
    phoneMasked: "039***729",
    liveGrade: "F",
    liveScore: 83,
    liveSummary: "SP: từ là tốt nhất trên thị trường rồi đấy anh ạ Nó về bón anh nhá · SL: 10 gói · Giá: 230k · Địa chỉ: thai nguyen · Chưa đồng ý nhận hàng · Cảm xúc: positive",
  },
];
