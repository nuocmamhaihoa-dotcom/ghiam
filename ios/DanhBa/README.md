# Danh bạ trên iPhone

App nạp liên hệ vào iPhone của bạn và xoá liên hệ bạn chọn. Máy tin nhà phát triển một lần. App hỏi quyền Danh bạ một lần.

## Cài để máy tin được ngay

1. Cắm iPhone bằng cáp, mở khoá máy, bấm Tin cậy máy tính nếu iPhone hỏi.
2. Trên máy Mac, mở `ios/DanhBa/DanhBa.xcodeproj`.
3. Chọn target DanhBa → Signing & Capabilities → Automatically manage signing → chọn Team là Apple ID của bạn.
4. Phía trên Xcode, chọn đúng iPhone đang cắm, bấm Run.

Lần đầu mở app, iPhone báo nhà phát triển chưa được tin. Làm đúng một lần:

1. Bấm Đóng trên hộp thoại đó.
2. Mở Cài đặt → Cài đặt chung → VPN và quản lý thiết bị.
3. Trong mục Ứng dụng nhà phát triển, bấm Apple ID vừa dùng để cài.
4. Bấm Tin cậy, rồi bấm Tin cậy lần nữa.
5. Mở lại app Danh bạ.

Những lần mở sau không hỏi tin lại, cho đến khi chứng chỉ cài đặt hết hạn. Apple ID miễn phí hết hạn khoảng 7 ngày; cài lại bằng Xcode rồi tin một lần nữa.

## Dùng app

1. Nhập tên danh bạ, ví dụ `Khach`.
2. Dán danh sách hoặc chọn file. Mỗi dòng một người, ví dụ `Trần Tùng, 0901234567`. Bấm Nạp hàng loạt.
3. Bấm Chia danh bạ. Cứ 5000 số thành một cuốn: `Khach 1`, `Khach 2`, … Số trùng chỉ giữ ở cuốn đầu.
4. Bấm Nạp vào iPhone. iPhone hỏi quyền Danh bạ một lần. Mỗi cuốn thành một nhóm trong Danh bạ.
5. Chạm một cuốn để chọn danh bạ đang dùng, rồi bấm Xoá danh bạ đang dùng. Các cuốn khác giữ nguyên.

Nếu đã từ chối quyền Danh bạ, app không hỏi lại. Bật trong Cài đặt. Xoá xong, máy đang đồng bộ iCloud cũng mất các số trong nhóm đó.
