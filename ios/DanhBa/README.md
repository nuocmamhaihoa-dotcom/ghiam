# Danh bạ trên iPhone

App này nạp liên hệ vào danh bạ iPhone của bạn và xoá những liên hệ bạn chọn. iPhone hỏi quyền Danh bạ một lần. Không có thao tác nào chạy nếu bạn chưa bấm Nạp hoặc Xoá.

Cài bằng Xcode trên máy Mac:

1. Mở `ios/DanhBa/DanhBa.xcodeproj`.
2. Chọn target DanhBa, ô Signing, chọn Team bằng Apple ID của bạn.
3. Cắm iPhone, chọn máy đó, bấm Run.
4. Nếu iPhone chưa tin máy Mac: Cài đặt → Cài đặt chung → VPN và quản lý thiết bị → tin cậy nhà phát triển.

Trong app:

- Tab Nạp: dán từng dòng `Tên` hoặc `Tên, 0901234567`, hoặc mở file `.csv`, `.txt`, `.vcf`. Bấm Nạp vào iPhone rồi xác nhận.
- Cùng tab đó có thể lấy tên đã lưu trên hub (`/v1/people`). Dán địa chỉ gốc của hub và token. Máy trong mạng nội bộ dùng được `http`. Máy trên Internet cần `https`.
- Tab Danh bạ: chọn liên hệ rồi xoá. Xoá liên hệ đã nạp chỉ xoá những người app này đã thêm trong lần cài hiện tại. Xoá xong, iCloud sẽ đồng bộ mất liên hệ đó trên các máy khác.
