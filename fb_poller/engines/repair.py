"""Reapply every comment-parse repair when the same defect shows up again.

Engines stay independent. This module is not an engine. ``scan_one`` runs
``heal_result`` on every result, including a crash, so a regression in one
parser is corrected before the table is printed.
"""

from __future__ import annotations

import ast
import html
import json
import re
from collections.abc import Iterable
from urllib.parse import unquote

from fb_poller.engines.guard import (
    comment_id,
    connection_rows,
    first_comment_id,
    first_person_name,
    nested_text,
    time_text,
    placeholder_text,
    redact_text,
    scalar_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

# Codes the healer can emit. Each one corresponds to a defect already seen
# or a close variant of one (chrome glued back on, a short duplicate, a
# stringified JSON row, a secret in an error, a bool id, an HTML entity).
REPAIR_CODES = (
    "normalize_text",
    "unescape_text",
    "strip_chrome_lines",
    "drop_placeholder",
    "cap_text",
    "expand_json_text",
    "clear_bool_id",
    "clear_chrome_author",
    "dedupe_comment_id",
    "prefer_longer_text",
    "recover_comments_from_error",
    "redact_error",
    "strip_glued_chrome",
    "strip_markup",
    "decode_percent",
    "comments_win",
    "clear_null_time",
)

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CHROME_LINE = re.compile(
    r"^(?:"
    r"like|liked|comment|share|reply|follow|unfollow|message|more|"
    r"thích|đã thích|bình luận|chia sẻ|trả lời|theo dõi|bỏ theo dõi|hủy theo dõi|nhắn tin|thêm|"
    r"liked by \S.{0,80}|được thích bởi \S.{0,80}|thích bởi \S.{0,80}|"
    r"most relevant|newest|most recent|mới nhất|phù hợp nhất|"
    r"see more|see less|xem thêm|"
    r"write a comment\.\.\.|write a comment…|write a comment|"
    r"viết bình luận\.\.\.|viết bình luận…|viết bình luận|"
    r"write a reply\.\.\.|write a reply…|write a reply(?:\s+to\s+\S+)?|"
    r"viết phản hồi\.\.\.|viết phản hồi…|viết phản hồi|"
    r"view more comments|xem thêm bình luận|"
    r"view \d+(?: more)? comments?|xem thêm \d+ bình luận|"
    r"hide \d+ replies|hide repl(?:y|ies)|ẩn \d+ phản hồi|ẩn phản hồi|"
    r"reply to \S.{0,60}|trả lời \S.{0,60}|"
    r"comment as \S.{0,60}|reply as \S.{0,60}|bình luận với tư cách \S.{0,80}|"
    r"most relevant comments|relevant comments|newest comments|bình luận phù hợp nhất|bình luận mới nhất|"
    r"top comments|bình luận hàng đầu|newest first|"
    r"pin comment|ghim bình luận|edit comment|chỉnh sửa bình luận|"
    r"mark as spam|đánh dấu là spam|"
    r"notify me|thông báo cho tôi|"
    r"following|đang theo dõi|"
    r"turn off notifications(?:\s+for\s+\S.{0,60})?|tắt thông báo(?:\s+cho\s+\S.{0,80})?|"
    r"\d+\s+người khác|"
    r"hide comment|ẩn bình luận|this comment was hidden|"
    r"view post|go to post|đi tới bài viết|"
    r"link copied|đã sao chép liên kết|"
    r"joined the conversation|đã tham gia cuộc trò chuyện|"
    r"delete comment|xóa bình luận|remove comment|gỡ bình luận|"
    r"block this person|chặn người này|mute this conversation|"
    r"translate comment|dịch bình luận|"
    r"see more from \S+|see less from \S+|"
    r"view less|ẩn bớt|"
    r"dịch bởi \S+(?:\s+\S+){0,2}|"
    r"comment settings|cài đặt bình luận|"
    r"who can see this comment\??|ai có thể xem bình luận này\??|"
    r"một lúc trước|vừa nãy|mới đây|recently|"
    r"(?:just now|vừa xong|recently|mới đây)\s*[·•∙‧⋅]\s*(?:edited|đã chỉnh sửa)|"
    r"hide or report this comment|ẩn hoặc báo cáo bình luận này|"
    r"comment options|tùy chọn bình luận|more options|tùy chọn khác|"
    r"unpin comment|bỏ ghim|feature this comment|unfeature|"
    r"limited profile|hồ sơ hạn chế|new contributor|người đóng góp mới|conversation starter|"
    r"commenting as \S.{0,60}|đang bình luận với tư cách \S.{0,80}|"
    r"write a public reply(?:\.\.\.|…)?|viết phản hồi công khai(?:\.\.\.|…)?|"
    r"view \d+ more repl(?:y|ies)|xem thêm \d+ phản hồi|"
    r"hide \d+ repl(?:y|ies)|show \d+ more comments?|hiển thị thêm(?: \d+)? bình luận|"
    r"no replies yet|chưa có phản hồi|be the first to reply|hãy là người đầu tiên trả lời|"
    r"this reply is unavailable|phản hồi này không còn|"
    r"comment hidden by page|bình luận đã bị trang ẩn|you hid this comment|bạn đã ẩn bình luận này|"
    r"khoảng\s+(?:một|hai|ba|an?|one)\s+(?:giây|phút|giờ|ngày|tuần|tháng|năm|second|minute|hour|day|week)s?(?:\s+(?:trước|ago))?|"
    r"reply from \S+|phản hồi từ \S+|"
    r"view insights|xem số liệu|"
    r"mark as unread|đánh dấu là chưa đọc|"
    r"find comments|tìm bình luận|"
    r"most relevant first|"
    r"cách đây\s+\d+\s+(?:giây|phút|giờ|tiếng|ngày|tuần|tháng|năm)|"
    r"\d+\s+(?:giây|phút|giờ|tiếng|ngày|tuần|tháng|năm)\s+trước đây|"
    r"(?:khoảng\s+)?\d+\s+tiếng(?:\s+trước)?|"
    r"a couple(?: of)? hours ago|half an hour ago|nửa giờ trước|"
    r"author liked this|tác giả đã thích|"
    r"you replied|bạn đã trả lời|"
    r"all replies|tất cả phản hồi|"
    r"report comment|báo cáo bình luận|"
    r"copy link(?:\s+to\s+comment)?|sao chép liên kết(?:\s+bình luận)?|"
    r"sort by(?:\s+\S.{0,40})?|sắp xếp theo(?:\s+\S.{0,40})?|"
    r"comments are turned off|comments disabled|bình luận đã tắt|"
    r"log in to comment|sign up to comment|đăng nhập để bình luận|"
    r"followed by \S.{0,80}|"
    r"see insights|"
    r"turn on notifications|bật thông báo|tắt thông báo|"
    r"hide all comments|ẩn tất cả bình luận|"
    r"unhide|bỏ ẩn|"
    r"why am i seeing this\??|"
    r"manage comments|quản lý bình luận|"
    r"who can comment\??|"
    r"rising contributor|group expert|new member|thành viên mới|"
    r"moderator|người kiểm duyệt|"
    r"pinned(?:\s+comment)?|bình luận đã ghim|đã ghim|ghim|"
    r"\d+\s+new\s+comments?|new comments|"
    r"(?:this )?comment(?: is)? hidden|hidden comment|bình luận đã bị ẩn|"
    r"load more repl(?:y|ies)|tải thêm phản hồi|"
    r"see all comments|view all comments|"
    r"(?:một|hai|ba)\s+(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"sponsored|được tài trợ|active now|đang hoạt động|"
    r"[·•∙‧⋅…]+|"
    r"all comments|tất cả bình luận|"
    r"top fan|rising fan|tác giả|author|admin|verified|đã xác minh|"
    r"just now|vừa xong|"
    r"yesterday(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)?|"
    r"hôm qua(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})?|"
    r"(?:today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?|"
    r"hôm nay(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"thứ\s+(?:hai|ba|tư|năm|sáu|bảy|nhật)(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"translated by \S.{0,40}|được dịch bởi \S.{0,40}|"
    r"[·•∙‧⋅]?\s*(?:about\s+|khoảng\s+)?(?:an?|one|\d+)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|"
    r"(?:khoảng\s+)?\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"\d+\s*(?:s|m|h|d|w|y)\s*[·•∙‧⋅]?|"
    r"\d+\s*(?:secs?|mins?|hrs?|mos?|yrs?|wks?|seconds?|minutes?|hours?|months?|years?|weeks?)(?:\s+ago)?|"
    r"\d+\s*(?:days?|weeks?|months?|years?)(?:\s+ago)?|"
    r"[·•∙‧⋅]?\s*\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?(?:\s*(?:gmt|utc))?|"
    r"[·•∙‧⋅]?\s*\d{1,2}h\d{2}|"
    r"\d{4}-\d{2}-\d{2}(?:[t ]\d{1,2}:\d{2}(?::\d{2})?(?:z|[+-]\d{2}:?\d{2})?)?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)?|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:ngày\s+)?\d{1,2}\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{4})?(?:\s+(?:lúc|vào)\s+\d{1,2}(?:[:h]\d{2})?)?|"
    r"\d{1,2}\s+thg\s+\d{1,2}(?:,\s*\d{4})?(?:\s+(?:lúc|vào)\s+\d{1,2}(?:[:h]\d{2})?)?|"
    r"see \d+ more comments?|"
    r"see \d+ repl(?:y|ies)(?:\s+from\s+\S+)?|xem \d+ phản hồi(?:\s+từ\s+\S+)?|"
    r"translated from \S.{0,40}|được dịch từ \S.{0,60}|"
    r"rate (?:this )?translation|đánh giá bản dịch|"
    r"love|haha|wow|sad|angry|care|yêu thích|buồn|phẫn nộ|"
    r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[kmb]|"
    r"\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[kmb]?)\s+(?:likes?|reactions?|comments?|shares?|lượt thích|bình luận|chia sẻ)|"
    r"view all(?: \d+)? repl(?:y|ies)|see all repl(?:y|ies)|hide all repl(?:y|ies)|xem tất cả(?: \d+)? phản hồi|ẩn tất cả phản hồi|"
    r"view \d+ repl(?:y|ies)|"
    r"\d+\s+repl(?:y|ies)(?:\s+from\s+\S+)?|\d+\s+phản hồi(?:\s+từ\s+\S+)?|"
    r"see previous comments|view previous comments|load more comments|"
    r"xem bình luận trước|xem các bình luận trước|tải thêm bình luận|"
    r"show more|show less|"
    r"last\s+(?:week|month|year)|tuần trước|tháng trước|năm trước|hôm kia|"
    r"write a public comment(?:\.\.\.|…)?|viết bình luận công khai(?:\.\.\.|…)?|"
    r"see who reacted(?:\s+to\s+this)?|see who liked(?:\s+this)?|xem ai đã thích(?:\s+nội\s+dung\s+này)?|"
    r"(?:all|tất cả)\s+\d+\s+(?:comments?|bình luận)|"
    r"(?:\S+\s+){1,2}and\s+\d+\s+others?|(?:\S+\s+){1,2}và\s+\d+\s+người khác|"
    r"and\s+\d+\s+others?|và\s+\d+\s+người khác|"
    r"send|gửi|hide|ẩn|"
    r"(?:khoảng\s+)?(?:a\s+few|vài)\s+(?:seconds?|minutes?|hours?|days?|weeks?|giây|phút|giờ|ngày|tuần)(?:\s+(?:ago|trước))?|"
    r"less than (?:an?|\d+)\s+(?:second|minute|hour)s?(?:\s+ago)?|"
    r"(?:a\s+moment|moments|seconds|minutes|hours)\s+ago|"
    r"vừa mới(?:\s+xong)?|"
    r"(?:hơn|over|more\s+than)\s+(?:an?|\d+)\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+(?:ago|trước))?|"
    r"\d+\s*[smhdwy]\s+ago|"
    r"today|hôm nay|"
    r"view more replies|see more replies|xem thêm phản hồi|"
    r"see attachment|view attachment|xem tệp đính kèm|"
    r"top contributor|"
    r"\S.{0,60}\s*[·•∙‧⋅]\s*(?:follow|theo dõi|top fan|rising fan|top contributor|verified|đã xác minh|author|admin|tác giả)|"
    r"filter|lọc|"
    r"(?:\d+|one|you|bạn)\s+likes?\s+this|"
    r"\d+\s+người\s+thích(?:\s+nội\s+dung\s+này)?|"
    r"see \d+ more repl(?:y|ies)|"
    r"be the first to comment|hãy là người đầu tiên bình luận|"
    r"no comments yet|chưa có bình luận(?:\s+nào)?|"
    r"older comments|bình luận cũ hơn|"
    r"view previous replies|see previous replies|load previous comments|"
    r"comment\s*[·•∙‧⋅]\s*\d+\s*[smhdwy]|"
    r"this comment is unavailable|comment removed|bình luận này không còn|"
    r"see translation(?:\s+from\s+\S+)?|hide translation|show translation|view original|auto-translated|was this translation helpful\??|xem bản dịch|ẩn bản dịch|"
    r"see original(?:\s+post|\s*\([^)\n]{0,80}\)|\s+in\s+\S.{0,40})?|hide original(?:\s*\([^)\n]{0,80}\)|\s+in\s+\S.{0,40})?|"
    r"xem bản gốc(?:\s*\([^)\n]{0,80}\))?|ẩn bản gốc(?:\s*\([^)\n]{0,80}\))?|"
    r"edited(?:(?:\s*[·•∙‧⋅]\s*|\s+)(?:"
    r"just now|vừa xong|recently|mới đây|"
    r"yesterday(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)?|hôm qua|"
    r"(?:a\s+moment|moments)\s+ago|"
    r"\d+\s*(?:mos?|yrs?|wks?)|"
    r"(?:khoảng\s+)?(?:a\s+few|vài)\s+(?:seconds?|minutes?|hours?|giây|phút|giờ)(?:\s+(?:ago|trước))?|"
    r"less than (?:an?|\d+)\s+(?:second|minute|hour)s?(?:\s+ago)?|"
    r"(?:a\s+moment|moments|seconds|minutes|hours)\s+ago|"
    r"vừa mới(?:\s+xong)?|"
    r"\d+\s*[smhdwy]|"
    r"\d+\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago|"
    r"about\s+(?:an?|\d+)\s+(?:second|minute|hour|day|week)s?\s+ago|"
    r"\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?|"
    r"\d{1,2}h\d{2}|"
    r"\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?|"
    r"\d{1,2}\s+thg\s+\d{1,2}"
    r"))?|"
    r"đã chỉnh sửa(?:(?:\s*[·•∙‧⋅]\s*|\s+)(?:\d+\s*[smhdwy]|\d+\s+(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|\d{1,2}:\d{2}|\d{1,2}h\d{2}|\d{1,2}\s+thg\s+\d{1,2}))?|"
    r"\d+\s*[smhdwy]\s*[·•∙‧⋅]\s*(?:edited|đã chỉnh sửa)?|"
    r"all reactions:?|tất cả cảm xúc:?"
    r")$",
    re.IGNORECASE,
)
_BOOL_ID = {"true", "false", "none", "null"}
_GRAPHQL_PREFIX = re.compile(r"^\s*for\s*\(\s*;;\s*\)\s*;\s*")
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_BREAK = re.compile(r"(?i)<br\s*/?>")
_TAG = re.compile(r"</?[A-Za-z][^>\n]*>")
_GLUED_CHROME = re.compile(
    r"(?:"
    r"\s+(?:"
    r"write a reply(?:\s+to\s+\S+)?(?:\.\.\.|…)?|viết phản hồi(?:\s+cho\s+\S+)?(?:\.\.\.|…)?|"
    r"write a comment(?:\.\.\.|…)?|viết bình luận(?:\.\.\.|…)?|"
    r"write a public comment(?:\.\.\.|…)?|viết bình luận công khai(?:\.\.\.|…)?|"
    r"view \d+ repl(?:y|ies)|show more|show less|"
    r"see translation(?:\s+from\s+\S+)?|hide translation|show translation|view original|auto-translated|was this translation helpful\??|xem bản dịch|ẩn bản dịch|"
    r"see original(?:\s+post|\s*\([^)\n]{0,80}\)|\s+in\s+\S.{0,40})?|hide original(?:\s*\([^)\n]{0,80}\)|\s+in\s+\S.{0,40})?|"
    r"xem bản gốc(?:\s*\([^)\n]{0,80}\))?|ẩn bản gốc(?:\s*\([^)\n]{0,80}\))?|"
    r"see more|see less|xem thêm|"
    r"view all(?: \d+)? repl(?:y|ies)|see all repl(?:y|ies)|hide all repl(?:y|ies)|xem tất cả(?: \d+)? phản hồi|ẩn tất cả phản hồi|"
    r"top fan|rising fan|"
    r"just now|vừa xong|"
    r"edited|đã chỉnh sửa|"
    r"\d+\s+(?:likes?|reactions?|comments?|shares?|lượt thích|bình luận|chia sẻ)|"
    r"hide \d+ replies|ẩn \d+ phản hồi|"
    r"view \d+(?: more)? comments?|see \d+ more comments?|xem thêm \d+ bình luận|"
    r"see \d+ repl(?:y|ies)(?:\s+from\s+\S+)?|xem \d+ phản hồi(?:\s+từ\s+\S+)?|"
    r"translated from \S.{0,40}|được dịch từ \S.{0,60}|"
    r"rate (?:this )?translation|đánh giá bản dịch|"
    r"\d+\s*[smhdwy]\s*[·•∙‧⋅]\s*(?:edited|đã chỉnh sửa)?"
    r")"
    r"|"
    r"\s*[·•∙‧⋅]\s*(?:"
    r"just now|vừa xong|"
    r"(?:about\s+|khoảng\s+)?(?:an?|one|\d+)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|"
    r"\d+\s*(?:s|m|h|d|w|y)|"
    r"\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"\d+\s+(?:likes?|reactions?|comments?|shares?|lượt thích|bình luận|chia sẻ)|"
    r"edited|đã chỉnh sửa"
    r")"
    r")+$",
    re.IGNORECASE,
)
_AUTHOR_STAMP = re.compile(
    r"(?:"
    r"\s*[·•∙‧⋅]\s*(?:"
    r"just now|vừa xong|"
    r"yesterday(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)?|"
    r"hôm qua(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})?|"
    r"(?:today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?|"
    r"hôm nay(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"thứ\s+(?:hai|ba|tư|năm|sáu|bảy|nhật)(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"(?:about\s+|khoảng\s+)?(?:an?|one|\d+)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|"
    r"(?:khoảng\s+)?\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"\d+\s*(?:s|m|h|d|w|y)|"
    r"\d+\s*(?:secs?|mins?|hrs?|mos?|yrs?|wks?|seconds?|minutes?|hours?|months?|years?|weeks?)(?:\s+ago)?|"
    r"\d+\s*(?:days?|weeks?|months?|years?)(?:\s+ago)?|"
    r"[·•∙‧⋅]?\s*\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?(?:\s*(?:gmt|utc))?|"
    r"[·•∙‧⋅]?\s*\d{1,2}h\d{2}|"
    r"\d{4}-\d{2}-\d{2}(?:[t ]\d{1,2}:\d{2}(?::\d{2})?(?:z|[+-]\d{2}:?\d{2})?)?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)?|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:ngày\s+)?\d{1,2}\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{4})?(?:\s+(?:lúc|vào)\s+\d{1,2}(?:[:h]\d{2})?)?|"
    r"\d{1,2}\s+thg\s+\d{1,2}(?:,\s*\d{4})?(?:\s+(?:lúc|vào)\s+\d{1,2}(?:[:h]\d{2})?)?|"
    r"last\s+(?:week|month|year)|tuần trước|tháng trước|năm trước|hôm kia|"
    r"(?:một|hai|ba)\s+(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"(?:khoảng\s+)?(?:a\s+few|vài)\s+(?:seconds?|minutes?|hours?|days?|weeks?|giây|phút|giờ|ngày|tuần)(?:\s+(?:ago|trước))?|"
    r"less than (?:an?|\d+)\s+(?:second|minute|hour)s?(?:\s+ago)?|"
    r"(?:a\s+moment|moments|seconds|minutes|hours)\s+ago|"
    r"vừa mới(?:\s+xong)?|"
    r"(?:hơn|over|more\s+than)\s+(?:an?|\d+)\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+(?:ago|trước))?|"
    r"\d+\s*[smhdwy]\s+ago|"
    r"today|hôm nay|"
    r"follow|theo dõi|top contributor|"
    r"edited|đã chỉnh sửa|"
    r"top fan|rising fan|rising contributor|group expert|new member|moderator|verified|đã xác minh|tác giả|author|admin"
    r")"
    r")+$",
    re.IGNORECASE,
)
_CHROME_AUTHOR = {
    "like",
    "reply",
    "share",
    "follow",
    "message",
    "top fan",
    "rising fan",
    "author",
    "tác giả",
    "thích",
    "trả lời",
    "chia sẻ",
    "theo dõi",
    "nhắn tin",
    "more",
    "thêm",
    "admin",
    "verified",
    "đã xác minh",
}


def heal_result(result: EngineResult) -> EngineResult:
    """Correct a scan result in place of the caller. Safe to call twice."""
    comments, codes = heal_comments(result.comments)
    error = result.error
    if error:
        redacted = redact_text(error)
        if redacted != error:
            codes.append("redact_error")
        error = redacted
    if not comments and error:
        recovered = _recover_from_error(result.error or error)
        if recovered:
            comments, extra = heal_comments(recovered)
            codes.extend(extra)
            codes.append("recover_comments_from_error")
            error = None
    ok = result.ok
    if comments:
        ok = True
        if error:
            codes.append("comments_win")
            error = None
    elif result.ok:
        ok = False
        error = error or "empty_after_repair"
    note = result.note
    unique = _unique(codes)
    if unique:
        suffix = "repaired:" + ",".join(unique)
        note = f"{note} {suffix}".strip() if note else suffix
    return EngineResult(
        engine=result.engine,
        rank=result.rank,
        ok=ok,
        comments=comments,
        error=error,
        note=note,
    )


def heal_comments(comments: list[PublicComment]) -> tuple[list[PublicComment], list[str]]:
    """Drop chrome, expand stringified rows, and keep the longer copy of one id."""
    staged: list[PublicComment] = []
    codes: list[str] = []
    for comment in comments:
        expanded = _expand_json_text(comment.text)
        if expanded is None:
            staged.append(comment)
            continue
        codes.append("expand_json_text")
        for item in expanded:
            staged.append(
                PublicComment(
                    text=item.text,
                    author_name=item.author_name or comment.author_name,
                    comment_id=item.comment_id or comment.comment_id,
                    created_time=item.created_time or comment.created_time,
                )
            )
    cleaned: list[PublicComment] = []
    for comment in staged:
        text, text_codes = _clean_text(comment.text)
        codes.extend(text_codes)
        if text is None:
            continue
        author, author_codes = _clean_author(comment.author_name)
        codes.extend(author_codes)
        cid, id_codes = _clean_id(comment.comment_id)
        codes.extend(id_codes)
        created, time_codes = _clean_time(comment.created_time)
        codes.extend(time_codes)
        cleaned.append(
            PublicComment(
                text=text,
                author_name=author,
                comment_id=cid,
                created_time=created,
            )
        )
    merged, merge_codes = _dedupe(cleaned)
    codes.extend(merge_codes)
    return merged, _unique(code for code in codes if code in REPAIR_CODES)


def _clean_text(text: str) -> tuple[str | None, list[str]]:
    codes: list[str] = []
    cleaned = text.replace("\u00a0", " ")
    spaced = cleaned.replace("\u200b", " ")
    without_hidden = _CONTROLS.sub("", _ZERO_WIDTH.sub("", cleaned))
    spaced = _CONTROLS.sub("", _ZERO_WIDTH.sub("", spaced))
    if without_hidden != cleaned:
        codes.append("normalize_text")
    cleaned = without_hidden
    if " " not in cleaned and _PERCENT.search(cleaned):
        decoded = unquote(cleaned)
        if decoded != cleaned:
            codes.append("decode_percent")
            cleaned = decoded
    decoded = cleaned
    for _ in range(5):
        again = html.unescape(decoded)
        if again == decoded:
            break
        decoded = again
    unescaped = decoded.replace("\u00a0", " ")
    if decoded != cleaned:
        codes.append("unescape_text")
    if unescaped != decoded:
        codes.append("normalize_text")
    cleaned = unescaped
    marked = _TAG.sub("", _BREAK.sub("\n", cleaned))
    if marked != cleaned:
        codes.append("strip_markup")
    cleaned = marked
    spaced = _TAG.sub("", _BREAK.sub("\n", html.unescape(spaced).replace("\u00a0", " ")))
    kept: list[str] = []
    removed_chrome = False
    removed_glued = False
    collapsed_lines = cleaned.splitlines()
    spaced_lines = spaced.splitlines()
    if len(spaced_lines) != len(collapsed_lines):
        spaced_lines = collapsed_lines
    for line, spaced_line in zip(collapsed_lines, spaced_lines):
        stripped = line.strip().strip(" ·•∙‧⋅…").strip()
        spaced_stripped = spaced_line.strip().strip(" ·•∙‧⋅…").strip()
        if not stripped and not spaced_stripped:
            continue
        if (stripped and _CHROME_LINE.match(stripped)) or (
            spaced_stripped and _CHROME_LINE.match(spaced_stripped)
        ):
            removed_chrome = True
            continue
        unglued = _GLUED_CHROME.sub("", stripped).strip()
        unglued_spaced = _GLUED_CHROME.sub("", spaced_stripped).strip()
        if unglued != stripped or unglued_spaced != spaced_stripped:
            removed_glued = True
            stripped = unglued.strip(" ·•∙‧⋅…").strip()
            if len(unglued_spaced.strip(" ·•∙‧⋅…").strip()) < len(stripped):
                stripped = unglued_spaced.strip(" ·•∙‧⋅…").strip()
        if not stripped:
            continue
        if _CHROME_LINE.match(stripped) or _CHROME_LINE.match(spaced_stripped):
            removed_chrome = True
            continue
        kept.append(stripped)
    if removed_glued:
        codes.append("strip_glued_chrome")
    if removed_chrome:
        codes.append("strip_chrome_lines")
    cleaned = "\n".join(kept).strip()
    if not cleaned or placeholder_text(cleaned):
        codes.append("drop_placeholder")
        return None, codes
    if len(cleaned) > 8000:
        cleaned = cleaned[:8000]
        codes.append("cap_text")
    return cleaned, codes


def _clean_author(author: str | None) -> tuple[str | None, list[str]]:
    text = scalar_text(author)
    if text is None:
        return None, []
    visible = _ZERO_WIDTH.sub("", html.unescape(text)).replace("\u00a0", " ").strip()
    codes: list[str] = []
    if visible != text.strip():
        codes.append("normalize_text")
    if not visible:
        return None, codes
    if visible.lower() in _CHROME_AUTHOR or _CHROME_LINE.match(visible):
        codes.append("clear_chrome_author")
        return None, codes
    unstamped = _AUTHOR_STAMP.sub("", visible).strip(" ·•∙‧⋅")
    if unstamped != visible:
        codes.append("strip_glued_chrome")
        visible = unstamped
    if not visible or visible.lower() in _CHROME_AUTHOR or _CHROME_LINE.match(visible):
        codes.append("clear_chrome_author")
        return None, codes
    return visible, codes


def _clean_id(value: str | None) -> tuple[str | None, list[str]]:
    cid = comment_id(value)
    if cid is None:
        return None, []
    if cid.lower() in _BOOL_ID:
        return None, ["clear_bool_id"]
    return cid, []


def _clean_time(value: str | None) -> tuple[str | None, list[str]]:
    text = scalar_text(value)
    if text is None:
        return None, []
    if text.lower() in _BOOL_ID:
        return None, ["clear_null_time"]
    return text, []


def _json_payload(text: str) -> object | None:
    stripped = _GRAPHQL_PREFIX.sub("", text.strip())
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return _literal_payload(stripped)


def _literal_payload(text: str) -> object | None:
    """A Python repr such as ``{'message': 'xin'}`` is not JSON."""
    snippet = _balanced_literal(text[:8000])
    if snippet is None:
        return None
    try:
        return ast.literal_eval(snippet)
    except (SyntaxError, ValueError, MemoryError):
        return None


def _balanced_literal(text: str) -> str | None:
    if not text or text[0] not in "{[":
        return None
    pairs = {"{": "}", "[": "]"}
    stack: list[str] = []
    quote: str | None = None
    escape = False
    for index, char in enumerate(text):
        if quote is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[: index + 1]
            continue
        if char in "}]":
            return None
    return None


def _expand_json_text(text: str) -> list[PublicComment] | None:
    payload = _json_payload(text)
    if payload is None:
        return None
    rows = _loose_comments(payload)
    if not rows:
        return None
    bare = _GRAPHQL_PREFIX.sub("", text.strip())
    if len(rows) == 1 and rows[0].text == bare:
        return None
    return rows


def _recover_from_error(error: str) -> list[PublicComment]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(error):
        if char not in "{[":
            continue
        try:
            payload, _end = decoder.raw_decode(error[index:])
        except json.JSONDecodeError:
            payload = _literal_payload(error[index:])
        if payload is None:
            continue
        rows = _loose_comments(payload)
        if rows:
            return rows
    return []


def _loose_comments(payload: object, depth: int = 0) -> list[PublicComment]:
    if depth > 6:
        return []
    if isinstance(payload, list):
        found: list[PublicComment] = []
        for item in payload:
            found.extend(_loose_comments(connection_rows([item])[0], depth + 1))
        return found
    if not isinstance(payload, dict):
        return []
    rendered = None
    for key in ("text", "message", "comment", "comment_text", "commentText", "body", "preferred_body", "content"):
        rendered = nested_text(payload.get(key))
        if rendered:
            break
    own: list[PublicComment] = []
    if rendered and not placeholder_text(rendered):
        own.append(
            PublicComment(
                text=rendered,
                author_name=first_person_name(
                    payload,
                    ("author", "author_name", "from", "profileName", "user_name", "name", "user", "profile", "owner"),
                ),
                comment_id=first_comment_id(payload),
                created_time=time_text(payload.get("created_time"))
                or time_text(payload.get("created_at"))
                or time_text(payload.get("date")),
            )
        )
    nested: list[PublicComment] = []
    for key in ("comments", "replies", "data", "results", "items"):
        value = payload.get(key)
        linked = connection_rows(value)
        target = linked if linked else value
        if isinstance(target, (list, dict)):
            nested.extend(_loose_comments(target, depth + 1))
    return own + nested


def _dedupe(comments: list[PublicComment]) -> tuple[list[PublicComment], list[str]]:
    chosen: dict[str, PublicComment] = {}
    order: list[str] = []
    codes: list[str] = []
    for comment in comments:
        key = f"id:{comment.comment_id}" if comment.comment_id else f"body:{comment.author_name}|{comment.text}"
        current = chosen.get(key)
        if current is None:
            chosen[key] = comment
            order.append(key)
            continue
        if len(comment.text) > len(current.text):
            author = comment.author_name or current.author_name
            created = comment.created_time or current.created_time
            chosen[key] = PublicComment(comment.text, author, comment.comment_id, created)
            codes.append("prefer_longer_text")
        else:
            codes.append("dedupe_comment_id")
    return [chosen[key] for key in order], codes


def _unique(codes: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return seen
