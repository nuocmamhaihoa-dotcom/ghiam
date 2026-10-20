# Python ngắn

Chạy file: `python3 ten.py`.

In ra: `print(2 + 2)`.

Hàm:

```python
def cong(a, b):
    return a + b
```

Đọc file chữ: `Path("a.txt").read_text(encoding="utf-8")`.
Ghi file: `Path("a.txt").write_text("hi\n", encoding="utf-8")`.

Danh sách: `ten = ["a", "b"]`. Từ điển: `diem = {"a": 1}`.

Bắt lỗi:

```python
try:
    int("x")
except ValueError:
    print("không phải số")
```

Thư viện chuẩn có sẵn: json, pathlib, urllib.request, sqlite3.
Không cài gói mới nếu người dùng chưa bảo.
