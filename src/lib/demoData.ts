import type { CallOutcome } from "./analyzeCall";

export type DemoCall = {
  title: string;
  industry: string;
  product: string;
  agentName: string;
  outcome: CallOutcome;
  durationSec: number;
  transcript: string;
};

export const DEMO_CALLS: DemoCall[] = [
  {
    title: "BHSK — chốt gói cơ bản",
    industry: "Bảo hiểm",
    product: "Gói sức khỏe Super Care",
    agentName: "Lan",
    outcome: "won",
    durationSec: 95,
    transcript: `Sale: Em chào anh Minh, em Lan bên Bảo Việt. Anh đang tiện nói chuyện khoảng 30 giây không ạ?
Khách: Ừ, nói nhanh đi.
Sale: Em gọi vì chương trình chăm sóc sức khỏe đang có ưu đãi khám miễn phí. Anh đang có bảo hiểm sức khỏe chưa ạ?
Khách: Có của công ty rồi.
Sale: Bảo hiểm công ty thường có trần nằm viện. Em đối chiếu giúp điểm trống — nếu trùng em không tư vấn thêm.
Khách: Để xem giá nào.
Sale: Gói cơ bản khoảng 15 nghìn/ngày, chi trả nằm viện đến 200 triệu, kèm ưu đãi khám miễn phí tháng này.
Khách: Nghe cũng được.
Sale: Anh cho em mã CCCD để giữ chỗ ưu đãi hôm nay, em gửi link đăng ký luôn nhé?
Khách: Ok em, gửi đi.`,
  },
  {
    title: "BHSK — mất đơn vì giá",
    industry: "Bảo hiểm",
    product: "Gói sức khỏe Super Care",
    agentName: "Hùng",
    outcome: "lost",
    durationSec: 55,
    transcript: `Sale: Alo anh, bên bảo hiểm này có gói mới anh mua không?
Khách: Đắt lắm, thôi.
Sale: Không đắt đâu anh, mua đi.
Khách: Không quan tâm.
Sale: Anh suy nghĩ lại nhé.
Khách: Gác máy.`,
  },
  {
    title: "TPCN — xử lý sợ không hiệu quả",
    industry: "Thực phẩm chức năng",
    product: "Viên uống xương khớp",
    agentName: "Mai",
    outcome: "won",
    durationSec: 110,
    transcript: `Sale: Em chào chị Hằng, em Mai từ AnCare. Em gọi chia sẻ chương trình dùng thử 7 ngày cho xương khớp — chị đang quan tâm không ạ?
Khách: Sợ không hiệu quả.
Sale: Em hiểu ạ. Sản phẩm có hoàn tiền 7 ngày nếu không phù hợp, kèm hướng dẫn dùng. Chị dùng thử trước rủi ro thấp hơn nhiều.
Khách: Giá sao?
Sale: Liệu trình 2 hộp hôm nay có quà tặng và miễn phí ship. Chị chốt em gửi link luôn nhé?
Khách: Được, gửi link đi.`,
  },
  {
    title: "TPCN — khách đang dùng đối thủ",
    industry: "Thực phẩm chức năng",
    product: "Collagen",
    agentName: "Tuấn",
    outcome: "callback",
    durationSec: 80,
    transcript: `Sale: Em chào chị, em Tuấn bên GlowPlus. Chị đang dùng collagen nào chưa ạ?
Khách: Đang dùng rồi.
Sale: Chị dùng được bao lâu và cảm nhận thế nào ạ? Bên em khác ở chỗ hấp thu nhanh và cam kết đổi trả.
Khách: Để suy nghĩ đã.
Sale: Em hiểu. Ưu đãi còn hôm nay. Chị tiện giờ nào em gọi lại tư vấn ngắn ạ?
Khách: Mai 10 giờ.`,
  },
  {
    title: "Điện máy — chốt máy lạnh",
    industry: "Điện máy / gia dụng",
    product: "Máy lạnh inverter 1HP",
    agentName: "Phúc",
    outcome: "won",
    durationSec: 90,
    transcript: `Sale: Em chào anh, em Phúc siêu thị Điện Máy Xanh. Máy lạnh inverter 1HP đang giảm 18% + lắp đặt tận nơi miễn phí — anh tiện nghe 20 giây không ạ?
Khách: Mua online rẻ hơn.
Sale: Em so giúp: bên em gồm VAT + lắp đặt + bảo hành chính hãng tận nơi. Tổng thường tối ưu hơn flash sale không kèm dịch vụ.
Khách: Lắp được cuối tuần không?
Sale: Được ạ. Em giữ giá flash + lịch lắp Chủ nhật. Anh chốt luôn, em tạo đơn nhé?
Khách: Chốt đi em.`,
  },
  {
    title: "Điện máy — để suy nghĩ",
    industry: "Điện máy / gia dụng",
    product: "Nồi chiên không dầu",
    agentName: "Nhung",
    outcome: "lost",
    durationSec: 45,
    transcript: `Sale: Chị ơi nồi chiên đang sale em báo giá luôn.
Khách: Để suy nghĩ.
Sale: Thôi chị cân nhắc nhé.
Khách: Ừ.`,
  },
  {
    title: "Giáo dục — chốt học thử IELTS",
    industry: "Giáo dục / khóa học",
    product: "IELTS 6.5",
    agentName: "Vy",
    outcome: "won",
    durationSec: 100,
    transcript: `Sale: Em chào bạn, em Vy từ SkyEng. Em gọi vì lộ trình IELTS 6.5 đang mở suất học thử miễn phí — bạn đang hướng tới điểm bao nhiêu ạ?
Khách: 6.5 nhưng không có thời gian.
Sale: Lộ trình có ca tối và bài khoảng 30 phút/ngày. Bạn trống tối thứ 3 hoặc 5 để em xếp lớp thử?
Khách: Tối thứ 3 được.
Sale: Em giữ suất học thử + ưu đãi học phí tuần này. Em gửi link đăng ký ca tối thứ 3 nhé?
Khách: Ok em.`,
  },
  {
    title: "BĐS — hẹn xem nhà",
    industry: "Bất động sản",
    product: "Căn hộ 2PN",
    agentName: "Khoa",
    outcome: "callback",
    durationSec: 95,
    transcript: `Sale: Em chào anh, em Khoa sàn Nhà Tốt. Em có suất căn 2PN view sông đang giữ chỗ — anh đang tìm ở hay đầu tư ạ?
Khách: Giá cao quá.
Sale: Em tách tiến độ thanh toán và hỗ trợ vay. Anh quanh ngân sách nào để em lọc căn phù hợp?
Khách: Sợ pháp lý.
Sale: Dự án có sổ hồng riêng. Em gửi hồ sơ pháp lý + lịch xem nhà thực tế cuối tuần được không ạ?
Khách: Chiều Chủ nhật được.`,
  },
  {
    title: "Bảo hiểm — xử lý nghi ngờ",
    industry: "Bảo hiểm",
    product: "Bảo hiểm nhân thọ",
    agentName: "Trang",
    outcome: "won",
    durationSec: 35,
    transcript: `Sale: Em chào chị, em Trang bên Prudential. Chị tiện trao đổi ngắn về quyền lợi bảo vệ gia đình không ạ?
Khách: Có thật không, sợ lừa.
Sale: Em hiểu lo của chị. Bên em có hợp đồng điện tử, bảo hành quyền lợi rõ và hàng nghìn khách đang tham gia. Em gửi mẫu quyền lợi + cam kết hoàn phí giai đoạn đầu để chị kiểm tra.
Khách: Vậy cũng được.
Sale: Chị cho em 10 phút hẹn online hôm nay để giải thích từng dòng phí — chị 7 giờ tối được không ạ?
Khách: Được, 7 giờ.`,
  },
  {
    title: "TPCN — pitch quá nhanh",
    industry: "Thực phẩm chức năng",
    product: "Omega 3",
    agentName: "Long",
    outcome: "lost",
    durationSec: 35,
    transcript: `Sale: Alo chị mua Omega 3 không giảm giá mạnh hôm nay chốt luôn đi chị ơi thanh toán giúp em đăng ký ngay.
Khách: Đang bận.
Sale: Nhanh thôi chị chốt đi ưu đãi mất.
Khách: Thôi em.`,
  },
];
