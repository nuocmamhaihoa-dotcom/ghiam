"""Optional control plane for Windows agents."""

import os

# libgomp chỉ đọc giới hạn này lúc được nạp, và PIL có thể nạp nó trước Tesseract.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
