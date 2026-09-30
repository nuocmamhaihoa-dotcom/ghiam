# Chuyển danh bạ sang VCF

Phần mềm desktop chuyển CSV, XLSX hoặc TXT thành các file vCard 3.0 để nhập vào Danh bạ iPhone. Chương trình chỉ đọc file người dùng chọn và không sửa file nguồn.

## Cách chạy khi đã có Python 3.12

```bash
cd contact_to_vcf
python3 -m pip install -r requirements.txt
python3 src/main.py
```

## Bản Windows không cần cài Python

Trên máy Windows:

```powershell
py -3.12 -m pip install -r contact_to_vcf\requirements.txt pyinstaller
powershell -ExecutionPolicy Bypass -File contact_to_vcf\build_windows.ps1
```

Kết quả:

```text
release/ContactToVCF.exe
release/README.txt
```

Workflow GitHub `contact-to-vcf-windows` cũng đóng gói file exe này khi có thay đổi trong `contact_to_vcf/`.

## Luồng sử dụng

1. Chọn file nguồn CSV, XLSX hoặc TXT.
2. Chọn cột tên và cột số điện thoại. Phần mềm không đoán cố định tên cột.
3. Đặt số liên hệ mỗi file. Mặc định là 5.000.
4. Chọn thư mục xuất.
5. Bật hoặc tắt lọc trùng, chuẩn hóa số, chuyển `0…` sang `+84…`, giữ tên gốc, chia thư mục.
6. Bấm **KIỂM TRA DỮ LIỆU** để đếm dòng hợp lệ, dòng lỗi và số trùng.
7. Bấm **BẮT ĐẦU CHUYỂN ĐỔI**.

Có thể **TẠM DỪNG**, **TIẾP TỤC** hoặc **HỦY**. Nếu máy tắt giữa chừng, mở lại, chọn đúng file nguồn và thư mục xuất, rồi bấm **TIẾP TỤC**. Phần mềm chỉ chạy tiếp khi file nguồn còn đúng kích thước và fingerprint đã lưu.

## Đầu vào

CSV:

```text
name,phone
Nguyen Van A,0901234567
```

XLSX dùng hàng đầu làm tiêu đề nếu bật tùy chọn đó. Cột số điện thoại trong Excel cần để dạng văn bản. Nếu Excel lưu `0901234567` thành số `901234567`, số 0 đầu đã mất trong file và phần mềm đưa dòng đó vào `errors.csv` thay vì tự thêm số 0.

TXT mặc định dùng dấu `|`. Có thể chọn dấu khác:

```text
Nguyen Van A|0901234567
```

## Số điện thoại

Khi bật chuẩn hóa và chuyển số Việt Nam:

| Đầu vào | Đầu ra |
|---|---|
| `0901234567` | `+84901234567` |
| `+84901234567` | `+84901234567` |
| `090 123 4567` | `+84901234567` |

Số không đủ cơ sở để nhận dạng được ghi vào `errors.csv`, không bị sửa đoán.

## Đầu ra

```text
output/contacts_00001.vcf
output/contacts_00002.vcf
output/errors.csv
output/report.txt
output/conversion.log
```

Mỗi contact:

```text
BEGIN:VCARD
VERSION:3.0
FN:Nguyen Van A
TEL;TYPE=CELL:+84901234567
END:VCARD
```

File dùng UTF-8 và xuống dòng CRLF. Tên Unicode, dấu tiếng Việt, dấu phẩy, dấu chấm phẩy và xuống dòng được thoát theo vCard để không làm vỡ contact phía sau.

`5.001` liên hệ hợp lệ tạo `contacts_00001.vcf` với 5.000 contact và `contacts_00002.vcf` với 1 contact.

Nếu bật chia thư mục, mỗi thư mục `batch_0001`, `batch_0002`, … chứa tối đa 1.000 file VCF.

Lọc trùng dùng SQLite trong `output/.contact_to_vcf/` và áp dụng cho toàn bộ phiên, không chỉ trong từng file. Dữ liệu được đọc và ghi theo từng khối, không nạp cả file lớn vào RAM.

Một dòng lỗi không dừng chương trình. Dòng đó nằm trong `errors.csv` với các cột `row_number`, `name`, `phone`, `error_reason`.

Sau mỗi file VCF, chương trình đếm `BEGIN:VCARD`, `END:VCARD` và kiểm tra `VERSION`, `FN`, `TEL`. File lệch cấu trúc không được đánh dấu hoàn thành.

## Kiểm thử

```bash
cd contact_to_vcf
python3 -m pytest tests -q
python3 scripts/benchmark.py 100000
```
