# Danh bạ trên iPhone

App nạp liên hệ vào iPhone của bạn và xoá liên hệ bạn chọn. Máy tin nhà phát triển một lần. App hỏi quyền Danh bạ một lần.

## Cài trên iPhone để tự chạy và tự cập nhật

File zip không chạy trên iPhone. App cài từ Safari:

1. Mở Safari trên iPhone, vào `/tai` của hub, bấm **Cài Danh bạ trên iPhone**. Đường thẳng là `/danhba/`.
2. Bấm Chia sẻ, rồi Thêm vào Màn hình chính.
3. Mở icon **Danh bạ**.

Lần sau phần mềm có bản mới, mở lại icon. iPhone tự lấy phiên bản đó khi có mạng. Không cần App Store, không cần Xcode, không cần cài lại.

Trong app: tên mặc định `Khach`. Dán số hoặc chọn file, bấm **Nạp lên iPhone**. App chia mỗi 5000 số một nhóm và đưa nhóm đó sang Danh bạ của máy. Một số chỉ nằm trong một nhóm. iPhone hỏi thì bấm **Thêm tất cả**.

Xoá một nhóm trên máy: trang web không được phép xoá số trong app Danh bạ. Bấm Xoá trong phần mềm để chuyển sang nhóm kế, rồi trong app Danh bạ của iPhone tìm đúng tên nhóm, ví dụ `Khach 1`, và xoá các số đó.

Gói `/tai/danhba.zip` là mã nguồn Xcode, dùng khi cần nhóm danh bạ thật và nút xoá xoá đúng nhóm trên máy. Cách cài Xcode nằm ở mục dưới.

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

1. Tên mặc định là `Khach`. Đổi nếu muốn tên khác. Các nhóm sẽ là `Khach 1`, `Khach 2`, …
2. Dán danh sách hoặc bấm Chọn file. Mỗi dòng một số là đủ, ví dụ `0901234567`. Có tên thì viết `Trần Tùng, 0901234567`.
3. Bấm **Nạp lên iPhone**. App tự chia mỗi 5000 số một nhóm, rồi nạp hết lên máy. Khi máy hỏi quyền, chọn **Cho phép đầy đủ**. Số trùng chỉ giữ ở nhóm đầu. Một số không nằm ở hai nhóm.
4. Chạm nhóm đang dùng. Dòng trạng thái ghi tên nhóm đó.
5. Xong nhóm thì bấm Xoá tên nhóm đó. App chuyển sang nhóm kế tiếp còn trên máy. Liên hệ sẵn có của bạn không bị xoá, chỉ được gỡ khỏi nhóm.

Nếu đã từ chối quyền, hoặc chọn Chỉ một số liên hệ, app không hỏi lại. Vào Cài đặt và chọn Cho phép đầy đủ. Xoá xong, máy đang đồng bộ iCloud cũng mất các số do app tạo trong nhóm đó.
