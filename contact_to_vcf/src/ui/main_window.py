"""Vietnamese desktop window for the contact converter."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.checkpoint import load_checkpoint
from models.records import Checkpoint, FileInspection, JobConfig, JobResult, Progress
from parsers.column_suggest import suggest_columns
from parsers.detect import detect_format
from processors.pool import CONTACTS_PER_FILE, export_book, list_books, pool_total
from ui.worker import ConversionWorker, InspectWorker, PoolImportWorker
from utils.format import format_duration, format_int, format_percent

_DELIMITERS = [
    ("Phẩy ,", ","),
    ("Chấm phẩy ;", ";"),
    ("Tab", "\t"),
    ("Gạch đứng |", "|"),
]
_PARKED_THREADS: list[QThread] = []


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Chuyển danh bạ sang VCF")
        self.resize(860, 640)
        self.inspection: FileInspection | None = None
        self.output_dir: Path | None = None
        self.worker: ConversionWorker | None = None
        self.pool_worker: PoolImportWorker | None = None
        self.inspect_worker: InspectWorker | None = None
        self._inspect_workers: list[InspectWorker] = []
        self._inspect_token = 0
        self._paused = False
        self._saved: Checkpoint | None = None
        self._saved_columns: tuple[int, int] | None = None
        self._auto_delimiter = False
        self._narrow: bool | None = None
        self._page_layout: QVBoxLayout | None = None
        self._build()
        self._apply_style()
        self._refresh_buttons()

    def _build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)
        root = QWidget()
        scroll.setWidget(root)
        layout = QVBoxLayout(root)
        self._page_layout = layout
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        title = QLabel("Danh bạ")
        title.setObjectName("title")
        hint = QLabel("Chọn file số, nạp vào kho, rồi tải từng danh bạ. Tên liên hệ là chính số điện thoại.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(title)
        layout.addWidget(hint)

        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("File CSV, Excel hoặc TXT")
        pick_file = QPushButton("Chọn file")
        pick_file.clicked.connect(self._pick_file)
        file_row.addWidget(self.file_edit, stretch=1)
        file_row.addWidget(pick_file)
        layout.addLayout(file_row)

        column_row = QHBoxLayout()
        column_row.addWidget(QLabel("Cột số"))
        self.phone_combo = QComboBox()
        self.name_combo = QComboBox()
        self.phone_combo.currentIndexChanged.connect(self._show_mapping)
        self.name_combo.currentIndexChanged.connect(self._show_mapping)
        self.delimiter_combo = QComboBox()
        for label, _value in _DELIMITERS:
            self.delimiter_combo.addItem(label)
        self.delimiter_combo.setCurrentIndex(3)
        self.delimiter_combo.currentIndexChanged.connect(self._reinspect)
        self.header_check = QCheckBox("Dòng đầu là tiêu đề")
        self.header_check.setChecked(True)
        self.header_check.stateChanged.connect(self._reinspect)
        column_row.addWidget(self.phone_combo, stretch=1)
        column_row.addWidget(self.delimiter_combo)
        column_row.addWidget(self.header_check)
        layout.addLayout(column_row)

        self.mapping_label = QLabel("Chọn file để xem vài dòng đầu.")
        self.mapping_label.setWordWrap(True)
        self.mapping_label.setObjectName("hint")
        layout.addWidget(self.mapping_label)
        self.preview = QTableWidget(0, 0)
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.preview.setMaximumHeight(120)
        self.preview.verticalHeader().setVisible(False)
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.preview)

        self.info_labels = {
            "name": QLabel("—"),
            "format": QLabel("—"),
            "rows": QLabel("—"),
            "valid": QLabel("—"),
            "invalid": QLabel("—"),
            "duplicate": QLabel("—"),
        }
        summary = QHBoxLayout()
        for key in ("format", "rows", "valid", "duplicate", "invalid"):
            summary.addWidget(self.info_labels[key])
        summary.addStretch(1)
        layout.addLayout(summary)

        kho_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText("Thư mục kho")
        pick_out = QPushButton("Chọn thư mục")
        pick_out.clicked.connect(self._pick_output)
        kho_row.addWidget(self.output_edit, stretch=1)
        kho_row.addWidget(pick_out)
        layout.addLayout(kho_row)

        self.start_btn = QPushButton("Nạp vào kho")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._import_pool)
        layout.addWidget(self.start_btn)

        self.plan_label = QLabel("Mỗi danh bạ 4.000 số. Số đã chia thì không chuyển danh bạ khác.")
        self.plan_label.setWordWrap(True)
        self.plan_label.setObjectName("hint")
        self.kho_label = QLabel("Chưa chọn thư mục kho.")
        self.kho_label.setWordWrap(True)
        layout.addWidget(self.plan_label)
        layout.addWidget(self.kho_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        layout.addWidget(self.progress_bar)

        book_row = QHBoxLayout()
        self.book_filter = QComboBox()
        self.book_filter.addItem("Tất cả", "all")
        self.book_filter.addItem("Chưa tải", "pending")
        self.book_filter.addItem("Đã tải", "downloaded")
        self.book_filter.currentIndexChanged.connect(self._reload_books)
        self.download_all_btn = QPushButton("Tải hết chưa tải")
        self.download_all_btn.clicked.connect(self._download_pending)
        book_row.addWidget(self.book_filter)
        book_row.addWidget(self.download_all_btn)
        book_row.addStretch(1)
        layout.addLayout(book_row)

        self.books = QTableWidget(0, 4)
        self.books.setHorizontalHeaderLabels(["Danh bạ", "Số lượng", "Trạng thái", ""])
        self.books.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.books.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.books.verticalHeader().setVisible(False)
        self.books.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.books.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.books.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.books.setMinimumHeight(160)
        self.books.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.books, stretch=1)

        self.more_check = QCheckBox("Hiện thêm")
        self.more_check.toggled.connect(self._toggle_more)
        layout.addWidget(self.more_check)
        self.more_panel = QWidget()
        more = QGridLayout(self.more_panel)
        more.setContentsMargins(0, 0, 0, 0)
        self.per_file = QSpinBox()
        self.per_file.setRange(1, 1_000_000)
        self.per_file.setValue(CONTACTS_PER_FILE)
        self.per_file.setGroupSeparatorShown(True)
        self.per_file.valueChanged.connect(self._update_plan)
        self.dedupe_check = QCheckBox("Loại bỏ số điện thoại trùng")
        self.dedupe_check.setChecked(True)
        self.normalize_check = QCheckBox("Chuẩn hóa số điện thoại")
        self.normalize_check.setChecked(True)
        self.vn_check = QCheckBox("Chuyển số Việt Nam 0… sang +84…")
        self.vn_check.setChecked(True)
        self.keep_name_check = QCheckBox("Giữ tên gốc")
        self.keep_name_check.setChecked(True)
        self.split_check = QCheckBox("Chia thư mục (1.000 file mỗi thư mục)")
        more.addWidget(QLabel("Cột tên"), 0, 0)
        more.addWidget(self.name_combo, 0, 1)
        more.addWidget(QLabel("Số mỗi file khi chuyển kiểu cũ"), 1, 0)
        more.addWidget(self.per_file, 1, 1)
        more.addWidget(self.dedupe_check, 2, 0, 1, 2)
        more.addWidget(self.normalize_check, 3, 0, 1, 2)
        more.addWidget(self.vn_check, 4, 0, 1, 2)
        more.addWidget(self.keep_name_check, 5, 0, 1, 2)
        more.addWidget(self.split_check, 6, 0, 1, 2)
        extra = QHBoxLayout()
        self.check_btn = QPushButton("Làm mới")
        self.pause_btn = QPushButton("Tạm dừng")
        self.resume_btn = QPushButton("Tiếp tục")
        self.cancel_btn = QPushButton("Hủy")
        self.check_btn.clicked.connect(self._reload_books)
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn.clicked.connect(self._resume)
        self.cancel_btn.clicked.connect(self._cancel)
        for button in (self.check_btn, self.pause_btn, self.resume_btn, self.cancel_btn):
            extra.addWidget(button)
        more.addLayout(extra, 7, 0, 1, 2)
        self.run_labels = {
            "processed": QLabel("0"),
            "total": QLabel("—"),
            "percent": QLabel("0%"),
            "speed": QLabel("0 dòng/giây"),
            "eta": QLabel("—"),
            "files": QLabel("0"),
            "errors": QLabel("0"),
        }
        stats = QHBoxLayout()
        for key in ("processed", "total", "percent", "speed", "eta", "files", "errors"):
            stats.addWidget(self.run_labels[key])
        more.addLayout(stats, 8, 0, 1, 2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setFixedHeight(90)
        self.log.setPlaceholderText("Nhật ký")
        more.addWidget(self.log, 9, 0, 1, 2)
        self.more_panel.setVisible(False)
        layout.addWidget(self.more_panel)
        self.setMinimumSize(360, 560)
        self._fit_screen()

    def _toggle_more(self, shown: bool) -> None:
        self.more_panel.setVisible(shown)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._fit_screen()

    def _fit_screen(self) -> None:
        if self._page_layout is None:
            return
        narrow = self.width() < 760
        if narrow == self._narrow:
            return
        self._narrow = narrow
        margin = 12 if narrow else 28
        self._page_layout.setContentsMargins(margin, 14, margin, 14)
        self._page_layout.setSpacing(10 if narrow else 8)
        button_height = 48 if narrow else 36
        self.start_btn.setMinimumHeight(button_height)
        self.download_all_btn.setMinimumHeight(button_height)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #ffffff; color: #1c1917; font-size: 14px; }
            QScrollArea { border: none; }
            QLabel#title { font-size: 22px; font-weight: 650; }
            QLabel#hint { color: #57534e; }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                background: #fafaf9;
                border: 1px solid #e7e5e4;
                border-radius: 8px;
                padding: 6px 8px;
                min-height: 22px;
            }
            QPlainTextEdit { background: #1c1917; color: #fafaf9; }
            QPushButton {
                background: #f5f5f4;
                color: #1c1917;
                border: 1px solid #e7e5e4;
                border-radius: 8px;
                padding: 8px 12px;
            }
            QPushButton:disabled { color: #a8a29e; }
            QPushButton#primary {
                background: #1c1917;
                color: white;
                border: none;
                font-weight: 650;
                padding: 10px 12px;
            }
            QPushButton#primary:disabled { background: #d6d3d1; color: white; }
            QHeaderView::section { background: #fafaf9; border: none; padding: 6px; }
            QProgressBar { border: none; border-radius: 4px; background: #f5f5f4; }
            QProgressBar::chunk { background: #1c1917; border-radius: 4px; }
            """
        )

    def _pick_file(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Chọn file nguồn",
            "",
            "Mọi định dạng (*.*)",
        )
        if not selected:
            return
        path = Path(selected)
        try:
            detect_format(path)
        except ValueError as exc:
            QMessageBox.warning(self, "File không hỗ trợ", str(exc))
            return
        self.file_edit.setText(str(path))
        self.header_check.blockSignals(True)
        self.delimiter_combo.blockSignals(True)
        suffix = path.suffix.lower()
        if suffix in {".csv", ".tsv"}:
            self.header_check.setChecked(True)
            self._auto_delimiter = True
        elif suffix in {".xlsx", ".xlsm", ".xltx"}:
            self.header_check.setChecked(True)
            self._auto_delimiter = False
        else:
            self.header_check.setChecked(False)
            self._auto_delimiter = True
        self.header_check.blockSignals(False)
        self.delimiter_combo.blockSignals(False)
        self._reinspect()

    def _pick_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất")
        if not selected:
            return
        self._use_output(Path(selected))

    def _use_output(self, folder: Path) -> None:
        self.output_dir = folder
        self.output_edit.setText(str(self.output_dir))
        checkpoint = load_checkpoint(self.output_dir)
        self._saved = checkpoint
        if checkpoint is not None:
            self._log(
                "Có tiến trình đã lưu: "
                f"đã xử lý {format_int(checkpoint.processed)} dòng, "
                f"{format_int(checkpoint.exported)} liên hệ đã xuất."
            )
            self._apply_checkpoint(checkpoint)
        self._reload_books()
        self._refresh_buttons()

    def _reinspect(self) -> None:
        text = self.file_edit.text().strip()
        if not text:
            return
        path = Path(text)
        if not path.is_file():
            return
        try:
            file_format = detect_format(path)
        except ValueError as exc:
            self._log(str(exc))
            return
        self._inspect_token += 1
        token = self._inspect_token
        self.info_labels["rows"].setText("Đang đếm…")
        auto = self._auto_delimiter
        self._auto_delimiter = False
        delimiter = None if file_format == "xlsx" or auto else self._delimiter()
        worker = InspectWorker(
            token,
            path,
            file_format,
            delimiter,
            self.header_check.isChecked(),
        )
        worker.finished_ok.connect(self._on_inspected)
        worker.failed.connect(self._on_inspect_failed)
        worker.finished.connect(self._release_finished_inspectors)
        self._inspect_workers = [item for item in self._inspect_workers if item.isRunning()]
        self._inspect_workers.append(worker)
        self.inspect_worker = worker
        worker.start()

    def _release_finished_inspectors(self) -> None:
        sender = self.sender()
        self._inspect_workers = [
            worker
            for worker in self._inspect_workers
            if worker is not sender and worker.isRunning()
        ]

    def _on_inspected(self, token: int, inspection: FileInspection) -> None:
        if token != self._inspect_token:
            return
        self.inspection = inspection
        if inspection.delimiter:
            self.delimiter_combo.blockSignals(True)
            self._set_delimiter(inspection.delimiter)
            self.delimiter_combo.blockSignals(False)
        self._fill_columns(inspection)
        self._fill_preview(inspection)
        self.info_labels["name"].setText(inspection.path.name)
        self.info_labels["format"].setText(inspection.file_format.upper())
        rows = "—" if inspection.estimated_rows is None else format_int(inspection.estimated_rows)
        self.info_labels["rows"].setText(rows)
        self._apply_saved_columns()
        self._show_mapping()
        self._update_plan()
        self._log(
            f"Đã đọc {inspection.path.name}: {len(inspection.columns)} cột, "
            f"dấu phân cách {self._delimiter_label(inspection.delimiter)}."
        )

    def _on_inspect_failed(self, token: int, message: str) -> None:
        if token != self._inspect_token:
            return
        self._log(message)

    def _fill_columns(self, inspection: FileInspection) -> None:
        self.name_combo.blockSignals(True)
        self.phone_combo.blockSignals(True)
        self.name_combo.clear()
        self.phone_combo.clear()
        for index, name in enumerate(inspection.columns):
            self.name_combo.addItem(name, index)
            self.phone_combo.addItem(name, index)
        if inspection.columns:
            name_index, phone_index = suggest_columns(inspection.columns, inspection.samples)
            self.name_combo.setCurrentIndex(name_index)
            self.phone_combo.setCurrentIndex(phone_index)
        self.name_combo.blockSignals(False)
        self.phone_combo.blockSignals(False)

    def _fill_preview(self, inspection: FileInspection) -> None:
        self.preview.clear()
        self.preview.setColumnCount(len(inspection.columns))
        self.preview.setHorizontalHeaderLabels(inspection.columns)
        self.preview.setRowCount(len(inspection.samples))
        for row_index, row in enumerate(inspection.samples):
            for column_index, value in enumerate(row):
                if column_index >= len(inspection.columns):
                    break
                self.preview.setItem(row_index, column_index, QTableWidgetItem(value))
        self._color_preview()

    def _show_mapping(self) -> None:
        phone = self.phone_combo.currentText() or "—"
        self.mapping_label.setText(
            f"Cột số điện thoại: {phone}. Mỗi số thành một liên hệ, tên cũng là số đó."
        )
        self._color_preview()

    def _color_preview(self) -> None:
        name_index = self.name_combo.currentIndex()
        phone_index = self.phone_combo.currentIndex()
        for row in range(self.preview.rowCount()):
            for column in range(self.preview.columnCount()):
                item = self.preview.item(row, column)
                if item is None:
                    continue
                if column == phone_index and phone_index != name_index:
                    item.setBackground(QColor("#d9f2e6"))
                elif column == name_index:
                    item.setBackground(QColor("#d9e8f6"))
                else:
                    item.setBackground(QColor("#ffffff"))

    def _update_plan(self) -> None:
        inspection = self.inspection
        if inspection is None or not inspection.estimated_rows:
            self.plan_label.setText(
                f"Mỗi danh bạ giữ tối đa {format_int(CONTACTS_PER_FILE)} số. "
                "Số đã vào danh bạ nào thì giữ nguyên danh bạ đó."
            )
            return
        files = (inspection.estimated_rows + CONTACTS_PER_FILE - 1) // CONTACTS_PER_FILE
        self.plan_label.setText(
            f"Khoảng {format_int(inspection.estimated_rows)} dòng, "
            f"khoảng {format_int(files)} danh bạ, mỗi danh bạ tối đa {format_int(CONTACTS_PER_FILE)} số. "
            "Số đã chia rồi không chuyển sang danh bạ khác."
        )

    def _delimiter_label(self, delimiter: str) -> str:
        for label, value in _DELIMITERS:
            if value == delimiter:
                return label
        if delimiter == "":
            return "không dùng"
        return repr(delimiter)

    def _delimiter(self) -> str:
        return _DELIMITERS[self.delimiter_combo.currentIndex()][1]

    def _set_delimiter(self, delimiter: str) -> None:
        for index, (_label, value) in enumerate(_DELIMITERS):
            if value == delimiter:
                self.delimiter_combo.setCurrentIndex(index)
                return

    def _apply_checkpoint(self, checkpoint: Checkpoint) -> None:
        current = self.file_edit.text().strip()
        if current and Path(current).resolve() != Path(checkpoint.input_path).resolve():
            self._log("Tiến trình đã lưu thuộc một file nguồn khác.")
            return
        self.header_check.blockSignals(True)
        self.delimiter_combo.blockSignals(True)
        self.header_check.setChecked(checkpoint.has_header)
        self._set_delimiter(checkpoint.delimiter)
        self.per_file.setValue(checkpoint.contacts_per_file)
        self.dedupe_check.setChecked(checkpoint.dedupe)
        self.normalize_check.setChecked(checkpoint.normalize_phone)
        self.vn_check.setChecked(checkpoint.vn_to_e164)
        self.keep_name_check.setChecked(checkpoint.keep_original_name)
        self.split_check.setChecked(checkpoint.split_directories)
        self.header_check.blockSignals(False)
        self.delimiter_combo.blockSignals(False)
        self._saved_columns = (checkpoint.name_column, checkpoint.phone_column)
        inspection = self.inspection
        needs_reread = inspection is not None and (
            inspection.has_header != checkpoint.has_header
            or (
                inspection.file_format != "xlsx"
                and inspection.delimiter != checkpoint.delimiter
            )
        )
        if needs_reread:
            self._reinspect()
            return
        self._apply_saved_columns()
        self._log("Đã khôi phục tùy chọn từ tiến trình đã lưu. Bấm Tiếp tục để chạy tiếp.")

    def _apply_saved_columns(self) -> None:
        if self._saved_columns is None or self.name_combo.count() == 0:
            return
        name_index, phone_index = self._saved_columns
        self._saved_columns = None
        if 0 <= name_index < self.name_combo.count():
            self.name_combo.setCurrentIndex(name_index)
        if 0 <= phone_index < self.phone_combo.count():
            self.phone_combo.setCurrentIndex(phone_index)

    def _config(self) -> JobConfig:
        if self.inspection is None:
            raise ValueError("Hãy chọn file nguồn")
        if self.output_dir is None and self.output_edit.text().strip():
            self.output_dir = Path(self.output_edit.text().strip())
        if self.output_dir is None:
            raise ValueError("Hãy chọn thư mục xuất")
        name_index = self.name_combo.currentData()
        phone_index = self.phone_combo.currentData()
        if name_index is None or phone_index is None:
            raise ValueError("Hãy chọn cột tên và cột số điện thoại")
        return JobConfig(
            input_path=self.inspection.path,
            output_dir=self.output_dir,
            file_format=self.inspection.file_format,
            delimiter=self.inspection.delimiter or self._delimiter(),
            has_header=self.header_check.isChecked(),
            name_column=int(name_index),
            phone_column=int(phone_index),
            contacts_per_file=self.per_file.value(),
            dedupe=self.dedupe_check.isChecked(),
            normalize_phone=self.normalize_check.isChecked(),
            vn_to_e164=self.vn_check.isChecked(),
            keep_original_name=self.keep_name_check.isChecked(),
            split_directories=self.split_check.isChecked(),
            encoding=self.inspection.encoding,
        )

    def _import_pool(self) -> None:
        if self._busy():
            return
        try:
            config = self._config()
        except ValueError as exc:
            QMessageBox.warning(self, "Thiếu thông tin", str(exc))
            return
        phone_index = self.phone_combo.currentData()
        if phone_index is None:
            QMessageBox.warning(self, "Thiếu thông tin", "Hãy chọn cột số điện thoại")
            return
        self.pool_worker = PoolImportWorker(
            config.output_dir,
            config.input_path,
            config.file_format,
            config.delimiter,
            config.has_header,
            config.encoding,
            int(phone_index),
            CONTACTS_PER_FILE,
        )
        self.pool_worker.progressed.connect(self._on_pool_progress)
        self.pool_worker.succeeded.connect(self._on_pool_done)
        self.pool_worker.failed.connect(self._on_failed)
        self.pool_worker.start()
        self._log(
            f"Đang nạp {config.input_path.name} vào kho, mỗi danh bạ {format_int(CONTACTS_PER_FILE)} số."
        )
        self._refresh_buttons()

    def _on_pool_progress(self, seen: int) -> None:
        self.run_labels["processed"].setText(format_int(seen))
        self.progress_bar.setRange(0, 0)

    def _on_pool_done(self, stats: object) -> None:
        added = int(getattr(stats, "added", 0))
        duplicate = int(getattr(stats, "duplicate", 0))
        rejected = int(getattr(stats, "rejected", 0))
        total = 0 if self.output_dir is None else pool_total(self.output_dir)
        self.info_labels["valid"].setText(format_int(added))
        self.info_labels["duplicate"].setText(format_int(duplicate))
        self.info_labels["invalid"].setText(format_int(rejected))
        self.run_labels["processed"].setText(format_int(int(getattr(stats, "seen", 0))))
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(1000)
        self._log(
            f"Nạp xong. Thêm {format_int(added)}, "
            f"trùng {format_int(duplicate)}, "
            f"không có chữ số {format_int(rejected)}. "
            f"Kho đang có {format_int(total)} số."
        )
        self._reload_books()
        self._refresh_buttons()

    def _reload_books(self) -> None:
        self.books.setRowCount(0)
        if self.output_dir is None:
            self.kho_label.setText("Chưa chọn thư mục kho.")
            return
        status = self.book_filter.currentData()
        if not isinstance(status, str):
            status = "all"
        books = list_books(self.output_dir, status)
        total = pool_total(self.output_dir)
        self.kho_label.setText(
            f"Kho đang giữ {format_int(total)} số. Mỗi danh bạ tối đa {format_int(CONTACTS_PER_FILE)} số."
        )
        self.books.setRowCount(len(books))
        for row, book in enumerate(books):
            self.books.setItem(row, 0, QTableWidgetItem(book.file_name))
            self.books.setItem(row, 1, QTableWidgetItem(format_int(book.contact_count)))
            if book.downloaded_at:
                state = f"Đã tải {book.downloaded_at}"
            else:
                state = "Chưa tải"
            self.books.setItem(row, 2, QTableWidgetItem(state))
            button = QPushButton("Tải về")
            button.clicked.connect(lambda _checked=False, book_id=book.id: self._download_book(book_id))
            self.books.setCellWidget(row, 3, button)

    def _download_book(self, book_id: int) -> None:
        if self.output_dir is None or self._busy():
            return
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Tải danh bạ",
            f"danhba_{book_id:05d}.vcf",
            "vCard (*.vcf)",
        )
        if not selected:
            return
        try:
            count = export_book(self.output_dir, book_id, Path(selected))
        except ValueError as exc:
            QMessageBox.warning(self, "Không tải được", str(exc))
            return
        self._log(f"Đã tải {Path(selected).name}: {format_int(count)} liên hệ.")
        self._reload_books()

    def _download_pending(self) -> None:
        if self.output_dir is None or self._busy():
            return
        pending = list_books(self.output_dir, "pending")
        if not pending:
            QMessageBox.information(self, "Kho số", "Không còn danh bạ chưa tải.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục để tải các danh bạ chưa tải")
        if not folder:
            return
        target = Path(folder)
        for book in pending:
            export_book(self.output_dir, book.id, target / book.file_name)
        self._log(f"Đã tải {len(pending)} danh bạ chưa tải vào {target}.")
        self._reload_books()

    def _busy(self) -> bool:
        converting = self.worker is not None and self.worker.isRunning()
        importing = self.pool_worker is not None and self.pool_worker.isRunning()
        return converting or importing

    def _check(self) -> None:
        self._launch("validate", resume=False)

    def _start_fresh(self) -> None:
        try:
            config = self._config()
        except ValueError as exc:
            QMessageBox.warning(self, "Thiếu thông tin", str(exc))
            return
        checkpoint = load_checkpoint(config.output_dir)
        existing = list(config.output_dir.glob("contacts_*.vcf")) if config.output_dir.exists() else []
        if checkpoint is not None or existing:
            answer = QMessageBox.question(
                self,
                "Bắt đầu lại",
                "Thư mục xuất đang có file VCF hoặc tiến trình đã lưu của phần mềm này. "
                "Bắt đầu lại sẽ ghi đè các file contacts_*.vcf, errors.csv và report.txt trong thư mục đó.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._launch("convert", resume=False)

    def _resume(self) -> None:
        if self.worker is not None and self.worker.isRunning() and self._paused:
            self.worker.control.resume()
            self._paused = False
            self._log("Tiếp tục xử lý.")
            self._refresh_buttons()
            return
        self._launch("convert", resume=True)

    def _pause(self) -> None:
        if self.pool_worker is not None and self.pool_worker.isRunning():
            self.pool_worker.request_cancel()
            self._log("Sẽ dừng sau số đang nạp.")
            return
        if self.worker is None or not self.worker.isRunning():
            return
        self.worker.control.pause()
        self._paused = True
        self._log("Đã tạm dừng.")
        self._refresh_buttons()

    def _cancel(self) -> None:
        if self.pool_worker is not None and self.pool_worker.isRunning():
            self.pool_worker.request_cancel()
            self._log("Sẽ dừng sau số đang nạp. Số đã vào kho vẫn giữ đúng danh bạ.")
            return
        if self.worker is None or not self.worker.isRunning():
            return
        self.worker.control.cancel()
        self._log("Đang hủy… Tiến trình đã lưu vẫn được giữ để tiếp tục sau.")

    def _launch(self, mode: str, resume: bool) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        try:
            config = self._config()
            config.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Thiếu thông tin", str(exc))
            return
        self._paused = False
        self.worker = ConversionWorker(config, mode, resume)
        self.worker.progress.connect(self._on_progress)
        self.worker.message.connect(self._log)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()
        self._refresh_buttons()

    def _on_progress(self, progress: Progress) -> None:
        self.run_labels["processed"].setText(format_int(progress.processed))
        self.run_labels["total"].setText(
            "—" if progress.total is None else format_int(progress.total)
        )
        self.run_labels["percent"].setText(format_percent(progress.percent))
        self.run_labels["speed"].setText(f"{format_int(int(progress.speed))} dòng/giây")
        self.run_labels["eta"].setText(format_duration(progress.eta_sec))
        self.run_labels["files"].setText(format_int(progress.files_created))
        self.run_labels["errors"].setText(format_int(progress.invalid))
        self.info_labels["valid"].setText(format_int(progress.valid))
        self.info_labels["invalid"].setText(format_int(progress.invalid))
        self.info_labels["duplicate"].setText(format_int(progress.duplicate))
        if progress.total:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(progress.percent * 10))
        else:
            self.progress_bar.setRange(0, 0)

    def _on_success(self, result: JobResult) -> None:
        self._paused = False
        self._on_progress(result.progress)
        if result.cancelled:
            self._log(result.message)
        elif result.report is not None:
            self._log("Đã ghi report.txt.")
            QMessageBox.information(self, "Hoàn tất", result.report.render())
        else:
            self._log(result.message)
        self._refresh_buttons()

    def _on_failed(self, message: str) -> None:
        self._paused = False
        self._log(message)
        QMessageBox.critical(self, "Lỗi", message)
        self._refresh_buttons()

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _refresh_buttons(self) -> None:
        running = self._busy()
        self.check_btn.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running and not self._paused)
        self.cancel_btn.setEnabled(running)
        can_resume = running and self._paused
        if not running and self.output_dir is not None and load_checkpoint(self.output_dir) is not None:
            can_resume = True
        self.resume_btn.setEnabled(can_resume)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.pool_worker is not None and self.pool_worker.isRunning():
            self.pool_worker.request_cancel()
            self.pool_worker.wait(5000)
            if self.pool_worker.isRunning():
                _park_thread(self.pool_worker)
        if self.worker is not None and self.worker.isRunning():
            self.worker.control.cancel()
            self.worker.wait(5000)
            if self.worker.isRunning():
                _park_thread(self.worker)
        for worker in list(self._inspect_workers):
            if not worker.isRunning():
                continue
            worker.wait(5000)
            if worker.isRunning():
                _park_thread(worker)
        self._inspect_workers = [
            worker for worker in self._inspect_workers if worker.isRunning()
        ]
        event.accept()


def _park_thread(thread: QThread) -> None:
    try:
        thread.disconnect()
    except RuntimeError:
        return
    _PARKED_THREADS.append(thread)

    def _release() -> None:
        if thread in _PARKED_THREADS:
            _PARKED_THREADS.remove(thread)

    thread.finished.connect(_release)
