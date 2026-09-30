"""Vietnamese desktop window for the contact converter."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.checkpoint import load_checkpoint
from models.records import FileInspection, JobConfig, JobResult, Progress
from parsers.detect import detect_format
from ui.worker import ConversionWorker, InspectWorker
from utils.format import format_duration, format_int, format_percent

_DELIMITERS = [
    ("Phẩy ,", ","),
    ("Chấm phẩy ;", ";"),
    ("Tab", "\t"),
    ("Gạch đứng |", "|"),
]


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

        options = QGroupBox("2. Cột, cỡ file và tùy chọn")
        form = QGridLayout(options)
        self.name_combo = QComboBox()
        self.phone_combo = QComboBox()
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
        form.addWidget(self.output_edit, 2, 2)
        form.addWidget(pick_out, 2, 3)
        form.addWidget(self.dedupe_check, 3, 0, 1, 2)
        form.addWidget(self.normalize_check, 3, 2, 1, 2)
        form.addWidget(self.vn_check, 4, 0, 1, 2)
        form.addWidget(self.keep_name_check, 4, 2, 1, 2)
        form.addWidget(self.split_check, 5, 0, 1, 4)
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
        else:
            self.header_check.setChecked(True)
            if path.suffix.lower() == ".csv":
                self.delimiter_combo.setCurrentIndex(0)
        self.header_check.blockSignals(False)
        self.delimiter_combo.blockSignals(False)
        self._reinspect()

    def _pick_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất")
        if not selected:
            return
        self.output_dir = Path(selected)
        self.output_edit.setText(str(self.output_dir))
        checkpoint = load_checkpoint(self.output_dir)
        if checkpoint is not None:
            self._log(
                "Có tiến trình đã lưu: "
                f"đã xử lý {format_int(checkpoint.processed)} dòng, "
                f"{format_int(checkpoint.exported)} liên hệ đã xuất."
            )
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
        worker = InspectWorker(
            token,
            path,
            file_format,
            self._delimiter() if file_format != "xlsx" else None,
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
        self._fill_columns(inspection)
        self.info_labels["name"].setText(inspection.path.name)
        self.info_labels["format"].setText(inspection.file_format.upper())
        rows = "—" if inspection.estimated_rows is None else format_int(inspection.estimated_rows)
        self.info_labels["rows"].setText(rows)
        self._log(f"Đã đọc cấu trúc {inspection.path.name}: {len(inspection.columns)} cột.")

    def _on_inspect_failed(self, token: int, message: str) -> None:
        if token != self._inspect_token:
            return
        self._log(message)

    def _fill_columns(self, inspection: FileInspection) -> None:
        self.name_combo.clear()
        self.phone_combo.clear()
        for index, name in enumerate(inspection.columns):
            self.name_combo.addItem(name, index)
            self.phone_combo.addItem(name, index)
        if inspection.columns:
            self.name_combo.setCurrentIndex(0)
            self.phone_combo.setCurrentIndex(1 if len(inspection.columns) > 1 else 0)

    def _delimiter(self) -> str:
        return _DELIMITERS[self.delimiter_combo.currentIndex()][1]

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
            self.worker.wait(3000)
        for worker in list(self._inspect_workers):
            if worker.isRunning():
                worker.wait(3000)
        self._inspect_workers = [worker for worker in self._inspect_workers if worker.isRunning()]
        event.accept()
