# doc_video — ghép số điện thoại, tên khách, ID từ video danh bạ TikTok

Đọc video quay màn hình (hoặc ảnh chụp): màn hình **Danh bạ** cho số điện thoại + tên,
màn hình **hồ sơ** cho tên + `@id`. Tên giống nhau thì ghép thành một hàng:

| Số điện thoại | Tên khách | ID |
|---|---|---|
| 0919313090 | Truc | @truc31798 |

## Cài một lần (Ubuntu)

```bash
sudo apt install -y tesseract-ocr tesseract-ocr-vie ffmpeg
pip install pillow numpy
# Bộ chữ lớn, đọc số chính xác hơn hẳn (nên có):
mkdir -p tessdata_best
for l in eng vie; do
  curl -fL -o tessdata_best/$l.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/$l.traineddata
done
```

## Chạy

```bash
python3 -m doc_video video.mp4 -o ket_qua.csv
python3 -m doc_video video1.mp4 video2.mp4 -o ket_qua.csv   # nhiều video, theo thứ tự quay
python3 -m doc_video anh1.jpg anh2.jpg --chi-tiet            # in những gì đọc được ở từng khung
```

Ra 3 file CSV (mở bằng Excel):

- `ket_qua.csv` — bảng chính 3 cột: số điện thoại, tên khách, ID. Chỉ chứa cặp chắc chắn.
- `ket_qua.can-xem.csv` — hồ sơ không tìm được số cùng tên, hoặc một tên có nhiều số.
- `ket_qua.chua-mo.csv` — số có trong danh bạ nhưng video chưa mở hồ sơ.

## Cách đọc

1. Bỏ dải đen quanh màn hình điện thoại, phóng ảnh lên đủ lớn.
2. Đọc cả màn hình để biết dòng nào ở đâu. Dòng toàn chữ số là số điện thoại, tên là dòng ngay dưới.
   Dòng có `@` là ID, tên là dòng ngay trên.
3. Cắt riêng từng dòng, phóng to, đọc lại bằng bộ đọc hợp loại dữ liệu:
   số và ID bằng bộ tiếng Anh chỉ cho chữ số/chữ không dấu (bộ tiếng Việt hay đọc 3 thành 5, 6 thành ó);
   tên đọc cả hai bộ rồi ghép từng ký tự: chữ có dấu theo bộ tiếng Việt, hoa thường và chữ số theo bộ không dấu.
4. Mỗi người hiện ở nhiều khung: lấy số và tên được đọc nhiều lần nhất.
5. Ghép theo tên, không phân biệt hoa thường/dấu/khoảng trắng. Một tên có nhiều số thì chọn số
   đang hiện trên màn hình danh bạ ngay trước khi mở hồ sơ; vẫn không chắc thì đưa vào bảng cần xem.
