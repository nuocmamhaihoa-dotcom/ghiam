from __future__ import annotations


class TikTokOsintError(Exception):
    """Base error for the public scanner."""


class InvalidProfileInput(TikTokOsintError):
    """Username or URL is not a public TikTok profile reference."""


class UnsafeUrl(TikTokOsintError):
    """URL is not safe to fetch (scheme, credentials, or non-public host)."""


class ProfileNotFound(TikTokOsintError):
    """Public page says this username does not exist."""

    def __init__(self, username: str) -> None:
        super().__init__(f"Không thấy hồ sơ công khai @{username}")
        self.username = username


class ProfileNotPublic(TikTokOsintError):
    """Page did not expose a public profile card (login wall or empty shell)."""

    def __init__(self, username: str, detail: str = "Không có thẻ hồ sơ công khai") -> None:
        super().__init__(f"@{username}: {detail}")
        self.username = username
        self.detail = detail


class TransientScrapeError(TikTokOsintError):
    """Network or page-load failure that may succeed on retry."""


class JobNotFound(TikTokOsintError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Không tìm thấy job {job_id}")
        self.job_id = job_id


class JobStateError(TikTokOsintError):
    """Job cannot accept this transition."""


class BookNotFound(TikTokOsintError):
    def __init__(self, book_id: str) -> None:
        super().__init__(f"Không tìm thấy danh bạ {book_id}")
        self.book_id = book_id


class SessionNotFound(TikTokOsintError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Không tìm thấy phiên ghi nhận {session_id}")
        self.session_id = session_id


class ValidationError(TikTokOsintError):
    """Caller input failed a product rule."""


class ReverseLookupForbidden(TikTokOsintError):
    """Phone → TikTok ID inference is intentionally unsupported."""

    def __init__(self) -> None:
        super().__init__(
            "Không triển khai tra cứu hoặc suy luận số điện thoại sang TikTok ID. "
            "Chỉ ghi nhận kết quả mà ứng dụng TikTok đã hiển thị cho chính người dùng "
            "sau khi họ tự cấp quyền trong ứng dụng."
        )


class OcrUnavailable(TikTokOsintError):
    """PaddleOCR is not installed in this environment."""


class ExportError(TikTokOsintError):
    """Export dependency or destination failed."""
