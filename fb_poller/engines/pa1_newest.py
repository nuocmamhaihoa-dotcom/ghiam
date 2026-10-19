"""Rank 5 — guest browser, Newest tab, first page only. Own browser, no shared pool."""

from __future__ import annotations

import re
from typing import Any

from fb_poller.engines.guard import invalid_limit, placeholder_text, public_error
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 5
NAME = "pa1-newest"
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad]")

_NEWEST_LABELS = ("Newest", "Mới nhất", "Most recent")
_DROP_LINE = re.compile(
    r"^(?:"
    r"like|liked|comment|share|reply|follow|unfollow|message|more|"
    r"thích|đã thích|bình luận|chia sẻ|trả lời|theo dõi|bỏ theo dõi|hủy theo dõi|nhắn tin|thêm|"
    r"liked by \S.{0,80}|được thích bởi \S.{0,80}|thích bởi \S.{0,80}|"
    r"most relevant|newest|most recent|mới nhất|phù hợp nhất|"
    r"see more|see less|xem thêm|"
    r"write a comment(?:\.\.\.|…)?|viết bình luận(?:\.\.\.|…)?|"
    r"write a reply(?:\s+to\s+\S+)?(?:\.\.\.|…)?|viết phản hồi(?:\s+cho\s+\S+)?(?:\.\.\.|…)?|"
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
    r"cách đây\s+(?:một|hai|ba|vài)\s+(?:giây|phút|giờ|tiếng|ngày|tuần|tháng|năm)|"
    r"seen by \S.{0,60}|đã xem bởi \S.{0,80}|"
    r"mark as read|đánh dấu là đã đọc|"
    r"this comment was removed|bình luận này đã bị xóa|comment deleted|đã xóa bình luận|"
    r"content unavailable|nội dung không có sẵn|"
    r"was this helpful\??|điều này có hữu ích không\??|đánh giá bản dịch này|thanks for your feedback|cảm ơn phản hồi của bạn|"
    r"hide post|ẩn bài viết|reply privately|trả lời riêng|message privately|nhắn tin riêng|"
    r"ban from group|cấm khỏi nhóm|remove from group|xóa khỏi nhóm|"
    r"switch profile|chuyển hồ sơ|hidden by page|bị trang ẩn|"
    r"earlier comments|bình luận trước đó|load earlier comments|tải bình luận trước|"
    r"show earlier replies|hiển thị phản hồi trước|view reply|xem phản hồi|xem \d+ phản hồi khác|"
    r"share to news feed|chia sẻ lên bảng tin|share to story|chia sẻ lên tin|"
    r"send in messenger|gửi trong messenger|send in whatsapp|"
    r"find support or report|tìm hỗ trợ hoặc báo cáo|not interested|không quan tâm|"
    r"unfollow \S.{0,60}|bỏ theo dõi \S.{0,80}|snooze \S.{0,40} for \d+ days|tạm ẩn \S.{0,40} trong \d+ ngày|"
    r"edited(?:\s*[·•∙‧⋅]\s*|\s+)\d+\s+(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"bỏ ghim bình luận|make top comment|đặt làm bình luận hàng đầu|"
    r"turn on comment ranking|bật xếp hạng bình luận|"
    r"newest comments first|bình luận mới nhất trước|"
    r"see original\s*[·•∙‧⋅]\s*translate|"
    r"dịch sang tiếng \S+|translate to \S+|"
    r"see more replies from \S+|xem thêm phản hồi từ \S+|"
    r"\d+\s+(?:likes?|lượt thích)\s*[·•∙‧⋅]\s*(?:reply|trả lời)|"
    r"(?:\S+\s+){1,3}(?:and|và)\s+\S+\s+(?:likes?\s+this|thích(?:\s+nội\s+dung\s+này)?)|"
    r"replying to \S+(?:'s comment)?|đang trả lời(?:\s+bình luận của)?\s+\S+|"
    r"view \d+ replies from \S+|hide replies from \S+|ẩn phản hồi từ \S+|"
    r"link to this comment|liên kết đến bình luận này|copy comment link|"
    r"share comment|chia sẻ bình luận|"
    r"edit history|lịch sử chỉnh sửa|edited by author|tác giả đã chỉnh sửa|"
    r"page liked this|trang đã thích(?:\s+nội\s+dung\s+này)?|"
    r"group admin|quản trị viên nhóm|group moderator|người kiểm duyệt nhóm|"
    r"community expert|chuyên gia cộng đồng|"
    r"top contributor this week|người đóng góp hàng đầu tuần này|"
    r"new to the group|mới vào nhóm|"
    r"viết câu trả lời(?:\.\.\.|…)?(?:\s+cho\s+\S+)?|public comment|bình luận công khai|"
    r"đã chỉnh sửa(?:\s*[·•∙‧⋅]\s*|\s+)(?:vừa xong|vừa nãy|mới đây|\d+\s+(?:giây|phút|giờ|ngày)(?:\s+trước)?)|"
    r"see translation\s*[·•∙‧⋅]\s*hide|xem bản dịch\s*[·•∙‧⋅]\s*ẩn|"
    r"\d+\s+repl(?:y|ies)\s*[·•∙‧⋅]\s*(?:like|reply)|\d+\s+phản hồi\s*[·•∙‧⋅]\s*thích|"
    r"like\s*[·•∙‧⋅]\s*reply(?:\s*[·•∙‧⋅]\s*\d+\s*[smhdwy])?|"
    r"thích\s*[·•∙‧⋅]\s*trả lời(?:\s*[·•∙‧⋅]\s*\d+\s*(?:giây|phút|giờ|ngày))?|"
    r"write a reply to \S+(?:'s comment)?|"
    r"liked your comment|đã thích bình luận của bạn|replied to you|đã trả lời bạn|"
    r"\d+\s+new repl(?:y|ies)|\d+\s+phản hồi mới|"
    r"also posting as \S+|đồng thời đăng với tư cách \S+|"
    r"end of comments|hết bình luận|"
    r"turn off commenting|tắt bình luận|turn on commenting|bật bình luận|"
    r"who can comment on this post\??|ai có thể bình luận về bài viết này\??|"
    r"filter comments|lọc bình luận|search comments|tìm kiếm bình luận|"
    r"show original|hiện bản gốc|show translated|hiện bản dịch|"
    r"view new comments|xem bình luận mới|"
    r"created with ai|được tạo bằng ai|ai generated|do ai tạo|"
    r"(?:just now|vừa xong)\s*[·•∙‧⋅]\s*(?:like|reply|share|thích|trả lời|chia sẻ)|"
    r"see less\s*[·•∙‧⋅]\s*translate|ẩn bớt\s*[·•∙‧⋅]\s*dịch|"
    r"load \d+ more comments?|tải thêm \d+ bình luận|"
    r"xem thêm \d+ câu trả lời|ẩn \d+ câu trả lời|"
    r"see previous replies from \S+|xem các phản hồi trước từ \S+|"
    r"author replied|tác giả đã trả lời|page replied|trang đã trả lời|"
    r"pinned by \S+|được ghim bởi \S+|"
    r"this comment is pending approval|bình luận này đang chờ phê duyệt|"
    r"pending approval|đang chờ phê duyệt|comment is pending|bình luận đang chờ duyệt|"
    r"marked as spam|đã đánh dấu là thư rác|unmark as spam|bỏ đánh dấu thư rác|"
    r"move to spam|chuyển vào thư rác|not spam|không phải thư rác|"
    r"turn on notifications for this comment|bật thông báo cho bình luận này|"
    r"stop seeing this|ngừng xem nội dung này|"
    r"(?:reply|like|share|thích|trả lời|chia sẻ)(?:\s*[·•∙‧⋅]\s*(?:reply|like|share|thích|trả lời|chia sẻ)){1,3}|"
    r"\d+\s*[smhdwy]\s*[·•∙‧⋅]\s*(?:like|reply|share|thích|trả lời)|"
    r"edited\s*[·•∙‧⋅]\s*(?:like|reply|share|thích|trả lời)|"
    r"see more\s*[·•∙‧⋅]\s*translate|xem thêm\s*[·•∙‧⋅]\s*dịch|"
    r"view \d+ more repl(?:y|ies) from \S+|"
    r"show \d+ earlier comments?|hiển thị \d+ bình luận trước|"
    r"featured comment|bình luận nổi bật|"
    r"\d+\s+reactions?\s*[·•∙‧⋅]\s*(?:reply|like)|"
    r"why am i seeing this comment\??|tại sao tôi nhìn thấy bình luận này\??|"
    r"tagged you in a comment|đã gắn thẻ bạn trong một bình luận|"
    r"shared your comment|đã chia sẻ bình luận của bạn|"
    r"see \S+ profile|view \S+ profile|xem trang cá nhân của \S+|"
    r"(?:add friend|message|follow|thêm bạn bè|nhắn tin|theo dõi)(?:\s*[·•∙‧⋅]\s*(?:add friend|message|follow|thêm bạn bè|nhắn tin|theo dõi)){1,2}|"
    r"comment ranking is on|đã bật xếp hạng bình luận|"
    r"no results found|không tìm thấy kết quả|"
    r"you're all caught up|bạn đã xem hết bình luận|"
    r"new comments will appear here|bình luận mới sẽ hiện ở đây|"
    r"jump to the latest comment|chuyển tới bình luận mới nhất|"
    r"hide replies|ẩn các câu trả lời|"
    r"featured by the page|được trang gắn nổi bật|"
    r"meta ai replied|"
    r"comment from the author|bình luận từ tác giả|"
    r"most relevant\s*[·•∙‧⋅]\s*all comments|"
    r"like\s*[·•∙‧⋅]\s*\d+\s*[·•∙‧⋅]\s*reply|thích\s*[·•∙‧⋅]\s*\d+\s*[·•∙‧⋅]\s*trả lời|"
    r"edited\s*[·•∙‧⋅]\s*\d+\s*[smhdwy]\s*[·•∙‧⋅]\s*(?:like|reply|thích|trả lời)|"
    r"(?:just now|vừa xong)\s*[·•∙‧⋅]\s*(?:reply|like)\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"send\s*[·•∙‧⋅]\s*like|gửi\s*[·•∙‧⋅]\s*thích|"
    r"translate\s*[·•∙‧⋅]\s*see original|dịch\s*[·•∙‧⋅]\s*xem bản gốc|"
    r"copy link\s*[·•∙‧⋅]\s*share|sao chép liên kết\s*[·•∙‧⋅]\s*chia sẻ|"
    r"report\s*[·•∙‧⋅]\s*block\s*[·•∙‧⋅]\s*hide|báo cáo\s*[·•∙‧⋅]\s*chặn\s*[·•∙‧⋅]\s*ẩn|"
    r"pin\s*[·•∙‧⋅]\s*unpin|ghim\s*[·•∙‧⋅]\s*bỏ ghim|"
    r"more\s*[·•∙‧⋅]\s*edit\s*[·•∙‧⋅]\s*delete|thêm\s*[·•∙‧⋅]\s*chỉnh sửa\s*[·•∙‧⋅]\s*xóa|"
    r"xem thêm câu trả lời|view profile|xem trang cá nhân|"
    r"message \S+|nhắn tin cho \S+|"
    r"hide from timeline|ẩn khỏi dòng thời gian|allow on timeline|cho phép trên dòng thời gian|"
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
    r"\d+\s*[smhdwy]\s*[·•∙‧⋅]\s*(?:edited|đã chỉnh sửa)?"
    r")$",
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
_GLUED_TAIL = re.compile(
    r"(?:\s+(?:"
    r"see more|see less|xem thêm|"
    r"see translation(?:\s+from\s+\S+)?|hide translation|show translation|view original|auto-translated|was this translation helpful\??|xem bản dịch|ẩn bản dịch|"
    r"view all(?: \d+)? repl(?:y|ies)|see all repl(?:y|ies)|hide all repl(?:y|ies)|"
    r"load more repl(?:y|ies)|tải thêm phản hồi"
    r"))+$",
    re.IGNORECASE,
)
_COUNT_LINE = re.compile(
    r"^(?:\d{1,7}|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[kmb])$",
    re.IGNORECASE,
)
_POST_LABELS = {"all reactions", "tất cả cảm xúc"}


def _is_post_block(block: str) -> bool:
    """The post article has a reaction label on its own line. A comment may mention the words."""
    for line in block.splitlines():
        label = line.strip().lower().rstrip(":").strip()
        if label in _POST_LABELS:
            return True
    return False


def comments_from_articles(blocks: list[str], limit: int) -> list[PublicComment]:
    """Turn visible article text into comments. Drops the post and UI chrome."""
    if limit < 1:
        return []
    found: list[PublicComment] = []
    seen: set[str] = set()
    for block in blocks:
        if _is_post_block(block):
            continue
        lines: list[str] = []
        for raw in block.splitlines():
            line = raw.strip().replace("\u00a0", " ")
            collapsed = _ZERO_WIDTH.sub("", line).strip()
            spaced = _ZERO_WIDTH.sub("", line.replace("\u200b", " ")).strip()
            if not collapsed or _DROP_LINE.match(collapsed) or _DROP_LINE.match(spaced):
                continue
            unglued = _GLUED_TAIL.sub("", collapsed).strip(" ·•∙‧⋅…").strip()
            unglued_spaced = _GLUED_TAIL.sub("", spaced).strip(" ·•∙‧⋅…").strip()
            if not unglued or _DROP_LINE.match(unglued) or _DROP_LINE.match(unglued_spaced):
                continue
            lines.append(unglued)
        if len(lines) > 2 and _COUNT_LINE.match(lines[-1]):
            lines.pop()
        if len(lines) < 2:
            continue
        author = _AUTHOR_STAMP.sub("", lines[0]).strip(" ·•∙‧⋅")
        text = " ".join(lines[1:]).strip()
        if len(author) < 1 or len(text) < 1 or _DROP_LINE.match(author) or placeholder_text(text):
            continue
        key = f"{author}|{text[:80]}"
        if key in seen:
            continue
        seen.add(key)
        found.append(PublicComment(text=text[:2000], author_name=author))
        if len(found) >= limit:
            break
    return found


def _browser_proxy(proxy: str) -> dict[str, str] | None:
    if not proxy.strip():
        return None
    from fb_poller.workers.proxy_manager import parse_proxy_line

    parsed = parse_proxy_line(proxy)
    if parsed is None:
        raise ValueError("proxy must be host:port, host:port:user:pass, or a URL")
    return parsed.as_playwright()


async def _dismiss_gate(page: Any) -> None:
    for label in ("Close", "Đóng"):
        loc = page.get_by_role("button", name=label)
        if await loc.count():
            try:
                await loc.first.click(timeout=1500)
            except Exception:
                return


async def scan(
    url: str,
    *,
    limit: int = 20,
    timeout_ms: int = 60000,
    proxy: str = "",
    **_: object,
) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return EngineResult(NAME, RANK, False, error=f"playwright_missing: {exc}")

    try:
        async with async_playwright() as pw:
            launch: dict[str, Any] = {"headless": True}
            browser_proxy = _browser_proxy(proxy)
            if browser_proxy:
                launch["proxy"] = browser_proxy
            browser = await pw.chromium.launch(**launch)
            page = await browser.new_page(
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if resp and resp.status >= 400:
                await browser.close()
                return EngineResult(NAME, RANK, False, error=f"http_{resp.status}")
            await page.wait_for_timeout(2500)
            await _dismiss_gate(page)
            for label in _NEWEST_LABELS:
                loc = page.get_by_text(label, exact=True)
                if await loc.count():
                    try:
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(800)
                    except Exception:
                        pass
                    break
            texts = await page.locator('[role="article"]').all_inner_texts()
            await browser.close()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    comments = comments_from_articles(texts, limit)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_visible_comments",
        note="Newest first page only. Nested replies are not expanded.",
    )
