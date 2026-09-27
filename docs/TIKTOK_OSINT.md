# TikTok Public OSINT Scanner

Công cụ thu thập **hồ sơ TikTok công khai** và trang liên kết công khai, rồi chuẩn hóa liên hệ để xuất CSV, Excel hoặc SQLite. Danh bạ điện thoại chỉ là dữ liệu của chính người dùng; không có bước suy luận số điện thoại sang tài khoản.

## Phạm vi

Có:

- Import username, `https://www.tiktok.com/@username`, hoặc short link `vm.tiktok.com` / `vt.tiktok.com`.
- Đọc thẻ hồ sơ công khai: username, nickname, bio, followers, likes, verified, link bio, cờ tài khoản riêng tư nếu TikTok vẫn hiện thẻ đó.
- Trích SĐT, email, Zalo, website, Facebook, Instagram, YouTube từ bio, nội dung link bio và OCR ảnh đại diện trên CDN TikTok.
- Chuẩn hóa, gộp trùng trong một hồ sơ, ghi `also_seen_on` khi cùng một giá trị xuất hiện ở hồ sơ khác.
- Retry, timeout, checkpoint, pause/resume, hàng đợi Redis (hoặc bộ nhớ nếu chưa cấu hình Redis).
- Xuất CSV, Excel, SQLite.
- Contact Sync Assistant: lưu danh bạ của người dùng, ghi username mà ứng dụng TikTok đã hiển thị, đối chiếu username đó với hồ sơ công khai đã quét.
- Quản lý nhiều danh bạ, sửa/xóa liên hệ, checkpoint và tiếp tục phiên ghi nhận, xuất danh bạ/kết quả ra CSV hoặc Excel.

Không có:

- Đăng nhập, cookie, hoặc API riêng tư.
- Tải danh bạ lên TikTok, băm số điện thoại, hoặc tra cứu SĐT → ID. `POST /api/official-sync/phone-to-id` luôn trả 403.
- Quét video, follower, comment, hoặc nội dung bị tường đăng nhập.

## Milestone

| Mốc | Nội dung | Trạng thái |
|---|---|---|
| M1 | Chuẩn hóa, trích xuất, chấm điểm, khử trùng, SQLite, xuất file | Có trong `tiktok_osint/domain` và `export` |
| M2 | Parser hồ sơ công khai, Playwright, retry/timeout, checkpoint/resume | Có trong `tiktok_osint/scrape` |
| M3 | FastAPI, hàng đợi, worker | Có trong `tiktok_osint/api` và `worker` |
| M4 | OCR ảnh đại diện qua PaddleOCR, cài đặt tùy chọn | Có trong `tiktok_osint/ocr` |
| M5 | Ghi nhận luồng danh bạ chính thức và dashboard Next.js | Có trong `tiktok_osint/sync` và `web/` |

## Đồng bộ Danh bạ TikTok theo quy trình chính thức

Module này là trợ lý ghi nhận; nó không tự đồng bộ với TikTok:

1. Người dùng nhập danh bạ của chính mình bằng biểu mẫu, danh sách nhiều dòng, CSV, TXT hoặc vCard. Số Việt Nam được chuẩn hóa về E.164 và lưu trong SQLite.
2. Người dùng tự mở ứng dụng TikTok chính thức trên điện thoại, vào phần tìm/thêm bạn bè hoặc cài đặt quyền riêng tư và chọn đồng bộ danh bạ. Tên mục có thể thay đổi theo phiên bản hoặc khu vực.
3. Người dùng đọc thông báo xin quyền của hệ điều hành và tự quyết định cấp quyền. Quyền có thể được thu hồi trong cài đặt TikTok hoặc điện thoại.
4. Người dùng tạo một phiên ghi nhận trong dashboard, rồi nhập **username hoặc URL hồ sơ** mà TikTok chính thức đã gợi ý/hiển thị.
5. Ứng dụng lưu checkpoint sau mỗi lượt, cho phép tạm dừng/tiếp tục. Đối chiếu với dữ liệu quét công khai chỉ dùng `tiktok_username`.
6. Danh bạ và kết quả phiên có thể tải xuống dạng CSV hoặc Excel.

Dashboard gom thành hai việc: lưu số vào danh bạ, rồi gắn `@username` TikTok đã hiển thị với từng liên hệ. Hướng dẫn trên điện thoại nằm cạnh ô ghi username. Phiên đang ghi được tự dùng lại; phiên tạm dừng được tiếp tục từ checkpoint.

Không có số điện thoại nào được dùng làm khóa tìm kiếm TikTok. Nhập số vào danh sách tài khoản hiển thị sẽ bị từ chối. Retry của module chỉ xử lý lỗi khóa SQLite tạm thời; lỗi chính sách và dữ liệu không hợp lệ không được retry.

## Chạy

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[tiktok,dev]"
playwright install chromium
tiktok-osint init-db
tiktok-osint import-profiles data/tiktok_profiles.sample.txt --job-name "dot-1"
tiktok-osint enqueue <job-id>
tiktok-osint worker
tiktok-osint export <job-id> --format csv
tiktok-osint serve
```

Dashboard:

```bash
cd web
npm install
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8088 npm run dev
```

OCR (không bắt buộc để chạy phần còn lại):

```bash
pip install paddleocr paddlepaddle
```

Nếu PaddleOCR chưa được cài, quét vẫn lưu bio và link bio; OCR bị bỏ qua và ghi log `ocr.unavailable`.

## Biến môi trường

Tiền tố `TIKTOK_` để không đụng cấu hình fb-poller.

```bash
TIKTOK_DATABASE_URL=sqlite:////absolute/path/tiktok_osint.db
TIKTOK_REDIS_URL=redis://127.0.0.1:6379/1
TIKTOK_REQUEST_TIMEOUT_SEC=20
TIKTOK_MAX_ATTEMPTS=3
TIKTOK_MIN_DELAY_SEC=1.5
TIKTOK_HEADLESS=true
```

SQLite là nguồn sự thật cho checkpoint. Redis chỉ đánh thức worker. Không có Redis thì worker vẫn nhận job `queued` từ SQLite.

## VPS

Script cài: `deploy/vps/install-tiktok-osint.sh`. Nginx phục vụ dashboard tĩnh và chuyển `/api/` vào API trên `127.0.0.1:8088`, có mật khẩu HTTP basic. Redis dùng DB index `/1`.

Worker Playwright cần glibc 2.28 trở lên (Ubuntu 20.04+). Ubuntu 18.04 chạy được API và dashboard; tiến trình worker không mở được Chromium trên bản đó.

## Kiểm thử

```bash
pytest tests/tiktok_osint -q
```

Bộ test không mở TikTok và không tải model OCR. Parser, retry, SSRF của link bio, và lệnh cấm tra cứu ngược được kiểm bằng dữ liệu giả.
