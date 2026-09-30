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
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
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
from ui.worker import ConversionWorker, InspectWorker
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
        self.resize(980, 780)
        self.inspection: FileInspection | None = None
        self.output_dir: Path | None = None
        self.worker: ConversionWorker | None = None
        self.inspect_worker: InspectWorker | None = None
        self._inspect_workers: list[InspectWorker] = []
        self._inspect_token = 0
        self._paused = False
        self._saved: Checkpoint | None = None
        self._saved_columns: tuple[int, int] | None = None
        self._auto_delimiter = False
        self._build()
        self._apply_style()
        self._refresh_buttons()

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        source_box = QGroupBox("1. File nguồn")
        source_layout = QGridLayout(source_box)
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("Chọn CSV, XLSX hoặc TXT")
        pick_file = QPushButton("Chọn file")
        pick_file.clicked.connect(self._pick_file)
        source_layout.addWidget(self.file_edit, 0, 0)
        source_layout.addWidget(pick_file, 0, 1)
        self.info_labels = {
            "name": QLabel("—"),
            "format": QLabel("—"),
            "rows": QLabel("—"),
            "valid": QLabel("—"),
            "invalid": QLabel("—"),
            "duplicate": QLabel("—"),
        }
        info = QFormLayout()
        info.addRow("Tên file", self.info_labels["name"])
        info.addRow("Định dạng", self.info_labels["format"])
        info.addRow("Tổng số dòng", self.info_labels["rows"])
        info.addRow("Số dòng hợp lệ", self.info_labels["valid"])
        info.addRow("Số dòng lỗi", self.info_labels["invalid"])
        info.addRow("Số điện thoại trùng", self.info_labels["duplicate"])
        source_layout.addLayout(info, 1, 0, 1, 2)
        layout.addWidget(source_box)

        preview_box = QGroupBox("Xem trước — kiểm tra cột tên và cột số trước khi chạy")
        preview_layout = QVBoxLayout(preview_box)
        self.preview = QTableWidget(0, 0)
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.preview.setMaximumHeight(150)
        self.preview.verticalHeader().setVisible(False)
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.mapping_label = QLabel("Chọn file để xem vài dòng đầu.")
        self.mapping_label.setWordWrap(True)
        preview_layout.addWidget(self.mapping_label)
        preview_layout.addWidget(self.preview)
        layout.addWidget(preview_box)

        options = QGroupBox("2. Cột, cỡ file và tùy chọn")
        form = QGridLayout(options)
        self.name_combo = QComboBox()
        self.phone_combo = QComboBox()
        self.name_combo.currentIndexChanged.connect(self._show_mapping)
        self.phone_combo.currentIndexChanged.connect(self._show_mapping)
        self.delimiter_combo = QComboBox()
        for label, _value in _DELIMITERS:
            self.delimiter_combo.addItem(label)
        self.delimiter_combo.setCurrentIndex(3)
        self.delimiter_combo.currentIndexChanged.connect(self._reinspect)
        self.header_check = QCheckBox("Dòng đầu là tiêu đề")
        self.header_check.setChecked(True)
        self.header_check.stateChanged.connect(self._reinspect)
        self.per_file = QSpinBox()
        self.per_file.setRange(1, 1_000_000)
        self.per_file.setValue(5000)
        self.per_file.setGroupSeparatorShown(True)
        self.per_file.valueChanged.connect(self._update_plan)
        size_500 = QPushButton("500 / file")
        size_500.setToolTip("Cỡ dễ nhập vào Danh bạ iPhone, từng file một.")
        size_500.clicked.connect(lambda: self.per_file.setValue(500))
        size_5000 = QPushButton("5.000 / file")
        size_5000.clicked.connect(lambda: self.per_file.setValue(5000))
        self.plan_label = QLabel("Chọn file để ước tính số file danh bạ.")
        self.plan_label.setWordWrap(True)
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText("Thư mục xuất")
        pick_out = QPushButton("Chọn thư mục")
        pick_out.clicked.connect(self._pick_output)
        self.dedupe_check = QCheckBox("Loại bỏ số điện thoại trùng")
        self.dedupe_check.setChecked(True)
        self.normalize_check = QCheckBox("Chuẩn hóa số điện thoại")
        self.normalize_check.setChecked(True)
        self.vn_check = QCheckBox("Chuyển số Việt Nam 0… sang +84…")
        self.vn_check.setChecked(True)
        self.keep_name_check = QCheckBox("Giữ tên gốc")
        self.keep_name_check.setChecked(True)
        self.split_check = QCheckBox("Chia thư mục (1.000 file mỗi thư mục)")
        form.addWidget(QLabel("Cột tên"), 0, 0)
        form.addWidget(self.name_combo, 0, 1)
        form.addWidget(QLabel("Cột số điện thoại"), 0, 2)
        form.addWidget(self.phone_combo, 0, 3)
        form.addWidget(QLabel("Dấu phân cách"), 1, 0)
        form.addWidget(self.delimiter_combo, 1, 1)
        form.addWidget(self.header_check, 1, 2, 1, 2)
        form.addWidget(QLabel("Số liên hệ mỗi file"), 2, 0)
        form.addWidget(self.per_file, 2, 1)
        form.addWidget(size_500, 2, 2)
        form.addWidget(size_5000, 2, 3)
        form.addWidget(self.plan_label, 3, 0, 1, 4)
        form.addWidget(self.output_edit, 4, 0, 1, 3)
        form.addWidget(pick_out, 4, 3)
        form.addWidget(self.dedupe_check, 5, 0, 1, 2)
        form.addWidget(self.normalize_check, 5, 2, 1, 2)
        form.addWidget(self.vn_check, 6, 0, 1, 2)
        form.addWidget(self.keep_name_check, 6, 2, 1, 2)
        form.addWidget(self.split_check, 7, 0, 1, 4)
        layout.addWidget(options)

        buttons = QHBoxLayout()
        self.check_btn = QPushButton("KIỂM TRA DỮ LIỆU")
        self.start_btn = QPushButton("BẮT ĐẦU CHUYỂN ĐỔI")
        self.pause_btn = QPushButton("TẠM DỪNG")
        self.resume_btn = QPushButton("TIẾP TỤC")
        self.cancel_btn = QPushButton("HỦY")
        self.check_btn.clicked.connect(self._check)
        self.start_btn.clicked.connect(self._start_fresh)
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn.clicked.connect(self._resume)
        self.cancel_btn.clicked.connect(self._cancel)
        for button in (
            self.check_btn,
            self.start_btn,
            self.pause_btn,
            self.resume_btn,
            self.cancel_btn,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        progress_box = QGroupBox("3. Tiến trình")
        progress_layout = QGridLayout(progress_box)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.run_labels = {
            "processed": QLabel("0"),
            "total": QLabel("—"),
            "percent": QLabel("0%"),
            "speed": QLabel("0 dòng/giây"),
            "eta": QLabel("—"),
            "files": QLabel("0"),
            "errors": QLabel("0"),
        }
        progress_layout.addWidget(self.progress_bar, 0, 0, 1, 4)
        pairs = [
            (1, 0, "Đã xử lý", "processed"),
            (1, 2, "Tổng số", "total"),
            (2, 0, "Phần trăm", "percent"),
            (2, 2, "Tốc độ xử lý", "speed"),
            (3, 0, "Thời gian còn lại", "eta"),
            (3, 2, "Số file đã tạo", "files"),
            (4, 0, "Số liên hệ lỗi", "errors"),
        ]
        for row, column, title, key in pairs:
            progress_layout.addWidget(QLabel(title), row, column)
            progress_layout.addWidget(self.run_labels[key], row, column + 1)
        layout.addWidget(progress_box)

        log_box = QGroupBox("Nhật ký hoạt động")
        log_layout = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setPlaceholderText("Nhật ký tổng hợp. Chi tiết lỗi nằm trong errors.csv và conversion.log.")
        log_layout.addWidget(self.log)
        layout.addWidget(log_box, stretch=1)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #f4f6f8; color: #1f2933; font-size: 13px; }
            QGroupBox {
                font-weight: 600;
                border: 1px solid #d5dde5;
                border-radius: 8px;
                margin-top: 12px;
                padding: 12px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                background: white;
                border: 1px solid #c9d3dd;
                border-radius: 6px;
                padding: 4px 6px;
            }
            QPlainTextEdit { background: #111827; color: #e5e7eb; }
            QPushButton {
                background: #0f6f8c;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 10px;
                font-weight: 600;
            }
            QPushButton:disabled { background: #b7c3ce; color: #f8fafc; }
            QProgressBar { border: 1px solid #c9d3dd; border-radius: 6px; background: white; text-align: center; }
            QProgressBar::chunk { background: #0f6f8c; border-radius: 5px; }
            """
        )

    def _pick_file(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Chọn file nguồn",
            "",
            "Danh bạ (*.csv *.xlsx *.txt)",
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
        if path.suffix.lower() == ".txt":
            self.header_check.setChecked(False)
            self.delimiter_combo.setCurrentIndex(3)
            self._auto_delimiter = False
        else:
            self.header_check.setChecked(True)
            self._auto_delimiter = path.suffix.lower() == ".csv"
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
        name = self.name_combo.currentText() or "—"
        phone = self.phone_combo.currentText() or "—"
        if self.name_combo.currentIndex() == self.phone_combo.currentIndex() and self.name_combo.count():
            self.mapping_label.setText(
                "Cột tên và cột số đang trùng nhau. Hãy chọn hai cột khác nhau."
            )
        else:
            self.mapping_label.setText(f"Cột tên: {name}. Cột số điện thoại: {phone}.")
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
        per_file = self.per_file.value()
        if inspection is None or not inspection.estimated_rows:
            self.plan_label.setText(
                f"Mỗi file tối đa {format_int(per_file)} liên hệ. "
                "500 liên hệ mỗi file thường dễ nhập vào Danh bạ iPhone hơn."
            )
            return
        files = (inspection.estimated_rows + per_file - 1) // per_file
        self.plan_label.setText(
            f"Khoảng {format_int(inspection.estimated_rows)} dòng sẽ chia thành "
            f"khoảng {format_int(files)} file, mỗi file tối đa {format_int(per_file)} liên hệ. "
            "Sau khi chạy, mở thu_tu_nhap.txt và nhập lần lượt."
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
        if self.worker is None or not self.worker.isRunning():
            return
        self.worker.control.pause()
        self._paused = True
        self._log("Đã tạm dừng.")
        self._refresh_buttons()

    def _cancel(self) -> None:
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
        running = self.worker is not None and self.worker.isRunning()
        self.check_btn.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running and not self._paused)
        self.cancel_btn.setEnabled(running)
        can_resume = running and self._paused
        if not running and self.output_dir is not None and load_checkpoint(self.output_dir) is not None:
            can_resume = True
        self.resume_btn.setEnabled(can_resume)

    def closeEvent(self, event) -> None:  # type: ignore[override]
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
