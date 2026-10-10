# VideoUp — tải video lên VPS khi khóa máy

App iPhone xếp hàng video trên máy, cắt khúc gửi lên hub (`/v1/videos/uploads…`). Mất mạng / khóa màn hình vẫn tiếp tục (URLSession nền). Máy chủ nhận xong mới đưa vào hàng đợi OCR.

## Cài bằng Xcode (một lần / mỗi máy)

1. Trên Mac: mở `ios/VideoUp/VideoUp.xcodeproj`.
2. Signing → Team = Apple ID của bạn.
3. Cắm iPhone, chọn máy, bấm Run.
4. Lần đầu: Cài đặt → Cài đặt chung → VPN và quản lý thiết bị → Tin cậy nhà phát triển.

Gói `/tai/videoup.zip` trên hub cũng là mã nguồn Xcode này.

## Dùng hàng ngày

1. Mở VideoUp → điền **URL hub** (`http://IP:8088`) và **Token**.
2. Đặt **Tên máy** (vd. iPhone An).
3. Chọn video từ Ảnh hoặc Files — có thể chọn nhiều.
4. Để app chạy / khóa máy; nhìn tiến độ từng mảnh.
5. Xong: mở trang video trên hub để xem hàng đợi đọc.

## Lưu ý

- Wi‑Fi ổn định vẫn tốt hơn 4G cho video lớn.
- Trùng file (cùng nội dung) sẽ báo đã có trong hàng đợi VPS.
- Safari vẫn dùng được (cũng cắt khúc + resume), nhưng **không** upload nền khi khóa máy — đó là việc của VideoUp.
