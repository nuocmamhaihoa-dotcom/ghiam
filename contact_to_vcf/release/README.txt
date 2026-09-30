CHUYỂN DANH BẠ SANG VCF
========================

Phần mềm đọc CSV, XLSX hoặc TXT và xuất file VCF cho Danh bạ iPhone.
File nguồn không bị sửa và không bị xóa.

CÁCH DÙNG
---------

1. Mở ContactToVCF.exe.
2. Chọn file nguồn.
3. Xem vài dòng đầu. Nền xanh lá là cột số, nền xanh dương là cột tên.
4. Giữ 5.000 liên hệ mỗi file, hoặc bấm 500 / file để nhập iPhone dễ hơn.
5. Chọn thư mục xuất.
6. Bấm KIỂM TRA DỮ LIỆU, rồi BẮT ĐẦU CHUYỂN ĐỔI.
7. Mở thu_tu_nhap.txt và nhập từng file VCF theo thứ tự.

Nút TẠM DỪNG / TIẾP TỤC / HỦY dùng trong lúc chạy.
Nếu chương trình bị tắt giữa chừng, chọn lại đúng file và thư mục xuất.
Phần mềm khôi phục cột và tùy chọn đã lưu. Bấm TIẾP TỤC.
Chỉ tiếp tục được khi file nguồn chưa đổi.
Khi chạy xong, file tạm để tiếp tục được xóa. Các file VCF vẫn được giữ.

FILE TẠO RA
-----------

contacts_00001.vcf
contacts_00002.vcf
thu_tu_nhap.txt
errors.csv
report.txt
conversion.log

Trong Excel, hãy đặt cột số điện thoại ở dạng văn bản trước khi lưu.
Hàng trống trong Excel được bỏ qua.
Nếu Excel làm mất số 0 ở đầu, dòng đó được ghi vào errors.csv.

VÍ DỤ SỐ VIỆT NAM
-----------------

0901234567      -> +84901234567
+84901234567    -> +84901234567
090 123 4567    -> +84901234567
