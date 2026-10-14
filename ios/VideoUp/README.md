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
4. Có thể rời app hoặc khóa máy. App đang mở thì gửi nhiều mảnh song song và đọc chữ bằng Apple Vision (tiếng Việt + tiếng Anh, tối đa khoảng 90 khung). Khi app vào nền, iOS 16 gửi nốt phần byte còn lại trong **một** `uploadTask(fromFile:)`. iOS 16 không có API tải tiếp của iOS 17: nếu lần gửi đó đứt, app được đánh thức, hỏi byte liền mạch trên máy chủ, rồi gửi một file đuôi mới.
5. Xong: mở trang video trên hub để xem hàng đợi đọc.

## Lưu ý

- Wi‑Fi ổn định vẫn tốt hơn 4G cho video lớn.
- Trùng file (cùng nội dung) sẽ báo đã có trong hàng đợi VPS.
- Safari vẫn cắt khúc và gửi lại được khi còn mở trang, nhưng **không** gửi tiếp khi người dùng rời Safari. Việc đó chỉ có trên VideoUp.
- Máy này không ký được app iPhone. Mở Xcode trên Mac, chọn Team, rồi Run lên máy.
