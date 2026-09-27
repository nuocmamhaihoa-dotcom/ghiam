from __future__ import annotations


class AppError(Exception):
    """Lỗi nghiệp vụ có thông báo hiển thị được cho người dùng."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class GoneError(AppError):
    status_code = 410


class PayloadTooLargeError(AppError):
    status_code = 413
