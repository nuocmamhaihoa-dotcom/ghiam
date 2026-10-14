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

VietOCR (không bắt buộc, đọc dấu tiếng Việt chuẩn hơn; mỗi tiến trình đọc dùng thêm khoảng 0,9 GB RAM):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install vietocr
mkdir -p models
curl -fL -o models/vgg_transformer.pth https://vocr.vn/data/vietocr/vgg_transformer.pth
```

Thiếu VietOCR thì bộ đọc chỉ dùng tesseract. Tắt hẳn bằng `DOC_VIDEO_VIETOCR=0`.

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

1. Bỏ dải đen quanh màn hình điện thoại, phóng ảnh lên đủ lớn. Giao diện tối thì đảo màu.
2. Đọc cả màn hình để biết dòng nào ở đâu. Dòng toàn chữ số là số điện thoại, tên là dòng ngay dưới.
   Dòng có `@` là ID, tên là dòng ngay trên. Không thấy `@` thì ID là dòng ngay trên hàng
   Đã follow / Follower / Thích. Vòng tròn chạm màn hình đè lên dòng chữ được bỏ ra.
3. Cắt riêng từng dòng, đọc lại bằng nhiều bộ đọc ở nhiều cỡ phóng, rồi bỏ phiếu theo từng ký tự:
   - tên: tesseract tiếng Việt (chữ cao khoảng 32 điểm ảnh), tesseract chỉ chữ không dấu và VietOCR
     (khoảng 80 điểm ảnh). Chữ số và hoa thường lấy theo bộ không dấu.
   - ID: sáu lần đọc tesseract ở các cỡ 28 đến 80 điểm ảnh, cộng VietOCR. ID TikTok luôn là chữ thường.
4. Mỗi người hiện ở nhiều khung: bỏ phiếu tên theo từng ký tự giữa các khung. Các khung của một lần
   mở hồ sơ được gộp, ID đọc lệch một ký tự dễ nhầm (l với 1, q với g) được gộp vào ID đọc nhiều hơn.
5. Ghép theo tên, @id trùng tên danh bạ, hoặc thứ tự mở hồ sơ khi đã mở hết một màn danh bạ.
   Không chắc thì đưa vào bảng cần xem.

Đo trên 400 ảnh danh bạ và hồ sơ dựng lại có đáp án, mỗi khung hình đọc riêng: tên danh bạ đúng
từng ký tự khoảng 97%, ID khoảng 93%, tên hồ sơ khoảng 90%. Video có nhiều khung cho mỗi người nên
kết quả cuối còn cao hơn. ID có hai gạch dưới liền nhau (`__`) đôi khi vẫn bị đọc thành một.
