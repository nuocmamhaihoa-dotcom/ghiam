# Danh bạ trên iPhone

App nạp liên hệ vào iPhone của bạn và xoá liên hệ bạn chọn. Máy tin nhà phát triển một lần. App hỏi quyền Danh bạ một lần.

## Nạp từ máy tính, dùng trên iPhone

Danh sách nằm trên hub, mỗi người một dòng gồm tên ghi nhớ và số điện thoại. Không gom mọi số vào một file chung.

1. Trên máy tính, mở `/danhba/nap` của hub. Dán danh sách hoặc chọn file, bấm **Nạp lên VPS**. Hub chia mỗi 5000 số một danh bạ. Một số chỉ nằm trong một danh bạ.
2. Trên iPhone, mở Safari vào `/danhba/`. Bấm **Nạp lên iPhone**. iPhone hỏi thì bấm **Thêm tất cả**.
3. Danh bạ vừa nạp chuyển sang mục **Đã dùng**. Các danh bạ chưa nạp ở mục **Chưa dùng**. Nếu bạn bấm Huỷ trên hộp của iPhone, xuất file danh bạ rồi bấm **Đối chiếu iPhone** và tick file toàn bộ để trả danh bạ về **Chưa dùng**.

## Khớp danh sách đã lưu với danh bạ trên iPhone

Trang web và máy tính cùng đọc một danh sách trên hub, tự cập nhật mỗi vài giây.

Safari không đọc được app Danh bạ. Để khớp theo số đang có trên máy:

1. Trong app Danh bạ, chia sẻ liên hệ ra file `.vcf`.
2. Trên trang iPhone, bấm **Đối chiếu iPhone** và chọn file đó. Danh bạ nào đủ số thì sang **Đã dùng**. Số lạ không bị thêm vào hub.
3. Chỉ tick **File này là toàn bộ danh bạ trên iPhone** khi file chứa hết số trên máy. Danh bạ đã dùng mà không còn số nào trong file sẽ về **Chưa dùng**.

Phím tắt của iPhone có thể gửi số tới `POST /v1/danhba/sync` với JSON `{"phones":["090..."],"full":false}`. App Xcode đọc được Danh bạ của máy, nhưng gói zip không cài được lên iPhone.

File zip không chạy trên iPhone.

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
