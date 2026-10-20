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
    r"(?:love|haha|wow|care|sad|angry)\s*[·•∙‧⋅]\s*\d+\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"yêu thích\s*[·•∙‧⋅]\s*\d+\s*[·•∙‧⋅]\s*trả lời|"
    r"public\s*[·•∙‧⋅]\s*friends\s*[·•∙‧⋅]\s*only me|"
    r"công khai\s*[·•∙‧⋅]\s*bạn bè\s*[·•∙‧⋅]\s*chỉ mình tôi|"
    r"gif\s*[·•∙‧⋅]\s*sticker\s*[·•∙‧⋅]\s*photo|"
    r"gif\s*[·•∙‧⋅]\s*nhãn dán\s*[·•∙‧⋅]\s*ảnh|"
    r"most relevant\s*[·•∙‧⋅]\s*newest|all comments\s*[·•∙‧⋅]\s*most relevant|"
    r"top fan\s*[·•∙‧⋅]\s*message|"
    r"view \d+ reply from \S+|"
    r"copy link\s*[·•∙‧⋅]\s*send in messenger|"
    r"edited\s*[·•∙‧⋅]\s*just now\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"replied to a comment you are following|"
    r"đã trả lời một bình luận bạn đang theo dõi|"
    r"reactions\s*[·•∙‧⋅]\s*\d+|see \d+ reactions|xem \d+ lượt bày tỏ cảm xúc|"
    r"all comments\s*[·•∙‧⋅]\s*newest|tất cả bình luận\s*[·•∙‧⋅]\s*mới nhất|"
    r"filter by\s*[·•∙‧⋅]\s*all comments|"
    r"(?:just now|vừa xong)\s*[·•∙‧⋅]\s*(?:edited|đã chỉnh sửa)\s*[·•∙‧⋅]\s*(?:like|reply|thích|trả lời)|"
    r"\d+\s*[smhdwy]\s*[·•∙‧⋅]\s*edited\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"edited\s*[·•∙‧⋅]\s*yesterday\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"(?:hide|report)\s*[·•∙‧⋅]\s*unfollow\s*[·•∙‧⋅]\s*(?:hide|report)|"
    r"edit\s*[·•∙‧⋅]\s*delete\s*[·•∙‧⋅]\s*report|"
    r"(?:react\s*[·•∙‧⋅]\s*reply|reply\s*[·•∙‧⋅]\s*react)|bày tỏ cảm xúc\s*[·•∙‧⋅]\s*trả lời|"
    r"gif\s*[·•∙‧⋅]\s*photo\s*[·•∙‧⋅]\s*sticker|"
    r"only you can see this comment|chỉ mình bạn nhìn thấy bình luận này|"
    r"reply in messenger|continue in messenger|"
    r"see edit history|view edit history|xem lịch sử chỉnh sửa|"
    r"mute this thread|"
    r"send a private reply|gửi phản hồi riêng|"
    r"following this comment|stop following this comment|đang theo dõi bình luận này|"
    r"photo\s*[·•∙‧⋅]\s*video\s*[·•∙‧⋅]\s*gif|"
    r"ảnh\s*[·•∙‧⋅]\s*video\s*[·•∙‧⋅]\s*(?:gif|nhãn dán)|"
    r"\d+\s+reactions?\s*[·•∙‧⋅]\s*\d+\s+repl(?:y|ies)|"
    r"(?:joined|member|admin|friends|following)\s*[·•∙‧⋅]\s*message|"
    r"(?:đã tham gia|thành viên|bạn bè|đang theo dõi)\s*[·•∙‧⋅]\s*nhắn tin|"
    r"collapse replies|expand replies|thu gọn câu trả lời|mở rộng câu trả lời|"
    r"see translation\s*[·•∙‧⋅]\s*see original|hide translation\s*[·•∙‧⋅]\s*see original|"
    r"xem bản dịch\s*[·•∙‧⋅]\s*xem bản gốc|ẩn bản dịch\s*[·•∙‧⋅]\s*xem bản gốc|"
    r"pin to top|unpin from top|ghim lên đầu|bỏ ghim khỏi đầu|"
    r"open in messenger|share to your story|chia sẻ lên tin của bạn|"
    r"quote this comment|trích dẫn bình luận này|"
    r"couldn't load comments|comments couldn't load|không tải được bình luận|"
    r"like page\s*[·•∙‧⋅]\s*follow\s*[·•∙‧⋅]\s*message|"
    r"pending\s*[·•∙‧⋅]\s*cancel request|"
    r"(?:accept|approve)\s*[·•∙‧⋅]\s*decline|chấp nhận\s*[·•∙‧⋅]\s*từ chối|"
    r"public\s*[·•∙‧⋅]\s*friends\s*[·•∙‧⋅]\s*custom|"
    r"công khai\s*[·•∙‧⋅]\s*bạn bè\s*[·•∙‧⋅]\s*tùy chỉnh|"
    r"save post\s*[·•∙‧⋅]\s*hide post|lưu bài viết\s*[·•∙‧⋅]\s*ẩn bài viết|"
    r"interested\s*[·•∙‧⋅]\s*going|"
    r"answer as page|replying as page|commenting as page|"
    r"notify me about replies|reply notifications are on|bật thông báo trả lời|"
    r"show \d+ more reply|view \d+ previous comments|"
    r"join group to comment|tham gia nhóm để bình luận|"
    r"save comment|unsave comment|lưu bình luận|bỏ lưu bình luận|"
    r"replies are turned off|đã tắt trả lời|"
    r"edit\s*[·•∙‧⋅]\s*delete\s*[·•∙‧⋅]\s*report|"
    r"chỉnh sửa\s*[·•∙‧⋅]\s*xóa\s*[·•∙‧⋅]\s*báo cáo|"
    r"friends' comments|comments from friends|bình luận từ bạn bè|"
    r"you can't reply to this comment|bạn không thể trả lời bình luận này|"
    r"suggested for you|đề xuất cho bạn|"
    r"why am i seeing this post\??|"
    r"find support or report comment|"
    r"friend request sent|đã gửi lời mời kết bạn|"
    r"choose a gif|chọn một gif|"
    r"helpful\s*[·•∙‧⋅]\s*not helpful|hữu ích\s*[·•∙‧⋅]\s*không hữu ích|"
    r"was this comment helpful\??|"
    r"reload comments|tải lại bình luận|"
    r"see more comments from \S+|hide comments from \S+|"
    r"request to join|yêu cầu tham gia|"
    r"held for review|đang chờ xét duyệt|"
    r"moved to spam|đã chuyển vào thư rác|"
    r"restore comment|khôi phục bình luận|"
    r"follow this thread|get notified about replies|"
    r"turn on reply notifications|"
    r"failed to send|retry send|gửi thất bại|thử gửi lại|"
    r"your comment is pending|waiting for approval|bình luận của bạn đang chờ duyệt|"
    r"all-star contributor\s*[·•∙‧⋅]\s*message|"
    r"group expert\s*[·•∙‧⋅]\s*message|"
    r"mutual friends\s*[·•∙‧⋅]\s*\d+|\d+\s+mutual friends|"
    r"share to a group|chia sẻ tới một nhóm|"
    r"view story|add to story|xem tin|thêm vào tin|"
    r"write the first comment|viết bình luận đầu tiên|"
    r"original poster|original commenter|người bình luận ban đầu|"
    r"community ambassador|top voice|"
    r"paid partnership|"
    r"you hid this post|you reported this comment|we removed this comment|"
    r"author comments only|show author comments|"
    r"visible to you|only you can see this|"
    r"the page limited who can comment|"
    r"slow mode is on|"
    r"followers only|members only|chỉ thành viên|chỉ người theo dõi|"
    r"comment hidden from your timeline|"
    r"show on timeline|don't show on timeline|"
    r"comment ranking|xếp hạng bình luận|"
    r"all-star contributor|visual storyteller|"
    r"joined the group \d+ years? ago|đã tham gia nhóm \d+ năm trước|"
    r"see all reviews|xem tất cả đánh giá|"
    r"helpful\s*[·•∙‧⋅]\s*\d+|hữu ích\s*[·•∙‧⋅]\s*\d+|not helpful|không hữu ích|"
    r"edited review|đã chỉnh sửa đánh giá|"
    r"đã chỉnh sửa\s*[·•∙‧⋅]\s*vừa xong\s*[·•∙‧⋅]\s*thích|"
    r"bình luận này không khả dụng|"
    r"lọc\s*[·•∙‧⋅]\s*tất cả bình luận|sắp xếp\s*[·•∙‧⋅]\s*mới nhất|"
    r"this translation is automatic|bản dịch này là tự động|"
    r"start the conversation|bắt đầu cuộc trò chuyện|"
    r"add a comment(?:\.\.\.|…)?|thêm bình luận(?:\.\.\.|…)?|"
    r"add a public comment(?:\.\.\.|…)?|"
    r"switch to your profile|use page identity|"
    r"posting as \S+|also comment as page|"
    r"double tap to like|press and hold to react|"
    r"choose a reaction|more reactions|remove reaction|change reaction|"
    r"unlike\s*[·•∙‧⋅]\s*reply|liked\s*[·•∙‧⋅]\s*reply|"
    r"see likes|see reactions|view reactions|view shares|"
    r"people who like this|people who reacted|"
    r"\d+\s+people? reacted|"
    r"\d+\s+likes?\s*[·•∙‧⋅]\s*\d+\s+repl(?:y|ies)|"
    r"\d+\s+comments?\s*[·•∙‧⋅]\s*\d+\s+shares?|"
    r"\d+\s+shares?\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"open thread|close thread|collapse thread|expand thread|"
    r"view full thread|see full conversation|continue this thread|"
    r"previous comments|next comments|newer comments|"
    r"refresh comments|updating comments|live comments|"
    r"top comments\s*[·•∙‧⋅]\s*live|"
    r"admins only|approved users only|"
    r"this is a preview|preview comment|scheduled comment|draft comment|"
    r"couldn't send|delete failed comment|"
    r"hidden from others|not visible to others|"
    r"spam folder|recover from spam|"
    r"this comment was hidden by the page|"
    r"allowed on your timeline|"
    r"unmute this thread|you follow this comment|"
    r"reply notifications are off|"
    r"\d+\s+person reacted|"
    r"ai info|comment insights|view comment insights|"
    r"this comment is from a page|posted by a page|verified page|"
    r"business\s*[·•∙‧⋅]\s*message|send message\s*[·•∙‧⋅]\s*like|"
    r"requested\s*[·•∙‧⋅]\s*cancel|cancel request|confirm\s*[·•∙‧⋅]\s*delete|"
    r"feature\s*[·•∙‧⋅]\s*unfeature|more\s*[·•∙‧⋅]\s*edit\s*[·•∙‧⋅]\s*hide|"
    r"see less\s*[·•∙‧⋅]\s*see original|see more\s*[·•∙‧⋅]\s*see original|"
    r"translate\s*[·•∙‧⋅]\s*hide translation|"
    r"edited\s+\d+\s*[smhdwy]\s*[·•∙‧⋅]\s*(?:like|reply)(?:\s*[·•∙‧⋅]\s*(?:like|reply))?|"
    r"(?:about an?|an?)\s+(?:minute|hour|day)\s+ago\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"show replies\s*[·•∙‧⋅]\s*\d+|replies\s*[·•∙‧⋅]\s*\d+\s*[·•∙‧⋅]\s*(?:like|reply)|"
    r"thread\s*[·•∙‧⋅]\s*\d+\s+repl(?:y|ies)|"
    r"continue reading|"
    r"read more\s*[·•∙‧⋅]\s*translate|read less\s*[·•∙‧⋅]\s*see original|"
    r"show less\s*[·•∙‧⋅]\s*translate|show more\s*[·•∙‧⋅]\s*see original|"
    r"facebook ambassador|rising voice|new friend|"
    r"message\s*[·•∙‧⋅]\s*wave|poke\s*[·•∙‧⋅]\s*message|"
    r"say thanks|send a sticker|search gifs|popular gifs|"
    r"stickers\s*[·•∙‧⋅]\s*gifs\s*[·•∙‧⋅]\s*photos|"
    r"feeling\s*[·•∙‧⋅]\s*activity|check in|tag people|tag friends|"
    r"background\s*[·•∙‧⋅]\s*aa|text color|"
    r"poll\s*[·•∙‧⋅]\s*\d+\s+options|yes\s*[·•∙‧⋅]\s*no\s*[·•∙‧⋅]\s*maybe|"
    r"upvote\s*[·•∙‧⋅]\s*downvote|"
    r"report a problem|something went wrong|try again|tap to retry|"
    r"mute \S+|restrict \S+|unfriend \S+|block \S+|report \S+|"
    r"why am i seeing ads\??|sponsored\s*[·•∙‧⋅]\s*like|brand collaboration|"
    r"insert emoji|chèn emoji|add a gif|thêm một gif|add photo|thêm ảnh|"
    r"add a photo or video|thêm ảnh hoặc video|"
    r"follow post|theo dõi bài viết|unfollow post|bỏ theo dõi bài viết|"
    r"give feedback or report this comment|góp ý hoặc báo cáo bình luận này|"
    r"snooze for \d+ days|tạm ẩn trong \d+ ngày|"
    r"unhide comment|bỏ ẩn bình luận|"
    r"open in new tab|mở trong tab mới|open post in new tab|mở bài viết trong tab mới|"
    r"go to profile|đi tới trang cá nhân|"
    r"shared with public|chia sẻ với công chúng|shared with friends|chia sẻ với bạn bè|"
    r"specific friends|bạn bè cụ thể|who can see this\??|ai có thể xem nội dung này\??|"
    r"commenting is off|đã tắt bình luận|"
    r"this comment was deleted\.?|bình luận này đã bị xóa\.?|"
    r"comment unavailable|bình luận không khả dụng|"
    r"suggest an edit|đề xuất chỉnh sửa|report translation|báo cáo bản dịch|"
    r"verified account|tài khoản đã xác minh|"
    r"people you may know|những người bạn có thể biết|"
    r"\d+\s+mutual friends?|\d+\s+bạn chung|"
    r"leave a comment(?:\.\.\.|…)?|để lại bình luận(?:\.\.\.|…)?|"
    r"say something(?:\.\.\.|…)?|nói gì đó(?:\.\.\.|…)?|"
    r"what'?s on your mind\??|bạn đang nghĩ gì\??|"
    r"post a comment|đăng bình luận|choose audience|chọn đối tượng|"
    r"comment controls|điều khiển bình luận|ranking settings|cài đặt xếp hạng|"
    r"sort comments|sắp xếp bình luận|send reaction|gửi cảm xúc|"
    r"leave a reply(?:\.\.\.|…)?|để lại phản hồi(?:\.\.\.|…)?|"
    r"mention someone|tag someone|gắn thẻ ai đó|add people|thêm người|"
    r"create poll|tạo cuộc thăm dò|"
    r"join conversation|tham gia cuộc trò chuyện|"
    r"leave conversation|rời khỏi cuộc trò chuyện|"
    r"start a conversation|bắt đầu cuộc trò chuyện|"
    r"show more replies|hiển thị thêm câu trả lời|"
    r"view replies|xem câu trả lời|load more replies|tải thêm câu trả lời|"
    r"jump to comments|chuyển tới bình luận|jump to replies|chuyển tới phản hồi|"
    r"this content isn'?t available right now|nội dung này hiện không khả dụng|"
    r"this page isn'?t available|trang này không khả dụng|"
    r"please try again later|vui lòng thử lại sau|"
    r"we'?re having trouble loading this|"
    r"download the app|tải ứng dụng|get the app|mở ứng dụng|"
    r"continue with browser|tiếp tục với trình duyệt|"
    r"log in to reply|đăng nhập để trả lời|create an account|tạo tài khoản|"
    r"add to your story|thêm vào tin của bạn|create a reel|tạo reel|"
    r"go live|phát trực tiếp|switch accounts|chuyển tài khoản|"
    r"continue as guest|tiếp tục với tư cách khách|"
    r"use without an account|see more on facebook|"
    r"don'?t show again|đừng hiện lại|remind me later|nhắc tôi sau|"
    r"maybe later|có lẽ để sau|ask me later|not now|không phải bây giờ|"
    r"give feedback|góp ý|help\s*&\s*support|trợ giúp\s*&\s*hỗ trợ|"
    r"settings\s*&\s*privacy|cài đặt\s*&\s*quyền riêng tư|"
    r"display\s*&\s*accessibility|hiển thị\s*&\s*trợ năng|"
    r"super reaction|siêu cảm xúc|raise hand|giơ tay|lower hand|hạ tay|"
    r"ask a question|đặt câu hỏi|friends except(?:\.\.\.|…)?|bạn bè ngoại trừ(?:\.\.\.|…)?|"
    r"embed post|nhúng bài viết|save post|lưu bài viết|"
    r"turn on notifications for this post|bật thông báo cho bài viết này|"
    r"turn off notifications for this post|tắt thông báo cho bài viết này|"
    r"share in messenger|chia sẻ trong messenger|"
    r"send as message|gửi dưới dạng tin nhắn|"
    r"reply privately in messenger|trả lời riêng trong messenger|"
    r"open original post|mở bài viết gốc|"
    r"view post insights|xem số liệu bài viết|"
    r"boost post|quảng cáo bài viết|edit post|chỉnh sửa bài viết|"
    r"delete post|xóa bài viết|change audience|thay đổi đối tượng|"
    r"invite friends|mời bạn bè|joined facebook|đã tham gia facebook|"
    r"why am i seeing this ad\??|tại sao tôi nhìn thấy quảng cáo này\??|"
    r"learn more about this ad|tìm hiểu thêm về quảng cáo này|"
    r"hide ad|ẩn quảng cáo|report ad|báo cáo quảng cáo|save ad|lưu quảng cáo|"
    r"can'?t go|không thể tham gia|"
    r"you were tagged|bạn đã được gắn thẻ|tagged in this|được gắn thẻ trong này|"
    r"mentioned you|đã nhắc đến bạn|also commented|cũng đã bình luận|also replied|cũng đã trả lời|"
    r"see who shared this|xem ai đã chia sẻ|"
    r"shared by \d+\s+people|được chia sẻ bởi \d+\s+người|"
    r"view \d+\s+shares?|xem \d+\s+lượt chia sẻ|"
    r"write a review|viết đánh giá|rate this page|đánh giá trang này|"
    r"was this review helpful\??|đánh giá này có hữu ích không\??|"
    r"write a recommendation|viết đề xuất|ask for recommendations|xin đề xuất|"
    r"not recommended|không được đề xuất|"
    r"you'?re a top fan|bạn là người hâm mộ hàng đầu|"
    r"become a top fan|trở thành người hâm mộ hàng đầu|"
    r"about this ad|về quảng cáo này|ad choices|lựa chọn quảng cáo|"
    r"your ad preferences|tuỳ chọn quảng cáo của bạn|"
    r"find support|tìm hỗ trợ|"
    r"privacy checkup|kiểm tra quyền riêng tư|"
    r"security and login|bảo mật và đăng nhập|"
    r"language and region|ngôn ngữ và khu vực|"
    r"keyboard shortcuts|phím tắt|"
    r"ad preferences|tuỳ chọn quảng cáo|"
    r"switch to the app|chuyển sang ứng dụng|"
    r"keep me logged in|giữ tôi đăng nhập|"
    r"already have an account\??|đã có tài khoản\??|"
    r"forgot password\??|quên mật khẩu\??|"
    r"edit profile|chỉnh sửa trang cá nhân|"
    r"your information|thông tin của bạn|"
    r"activity log|nhật ký hoạt động|"
    r"privacy shortcuts|phím tắt quyền riêng tư|"
    r"news feed|bảng tin|log out|đăng xuất|"
    r"reviews?\s*[·•∙‧⋅]\s*\d+|đánh giá\s*[·•∙‧⋅]\s*\d+|"
    r"photos?\s*[·•∙‧⋅]\s*\d+|ảnh\s*[·•∙‧⋅]\s*\d+|"
    r"videos?\s*[·•∙‧⋅]\s*\d+|check-?ins?\s*[·•∙‧⋅]\s*\d+|"
    r"\d+\s+reviews?|\d+\s+đánh giá|"
    r"works at \S.{0,60}|làm việc tại \S.{0,60}|"
    r"lives in \S.{0,60}|sống tại \S.{0,60}|"
    r"active \d+\s*[smhdwy]\s+ago|"
    r"đang hoạt động \d+\s+(?:giây|phút|giờ|ngày)(?:\s+trước)?|"
    r"send gift|gửi quà|buy now|mua ngay|shop now|order now|đặt hàng ngay|"
    r"get offer|claim offer|nhận ưu đãi|visit website|truy cập trang web|"
    r"call now|gọi ngay|get directions|chỉ đường|"
    r"send whatsapp message|message on whatsapp|nhắn tin whatsapp|"
    r"book now|đặt chỗ ngay|follow page|theo dõi trang|"
    r"liked page|unlike page|bỏ thích trang|"
    r"invite to group|mời vào nhóm|invite to event|mời đến sự kiện|"
    r"share group|chia sẻ nhóm|leave group|rời nhóm|report group|báo cáo nhóm|"
    r"group rules|quy tắc nhóm|about this group|về nhóm này|"
    r"see all members|xem tất cả thành viên|add members|thêm thành viên|"
    r"pending posts|bài viết đang chờ|member requests|yêu cầu thành viên|"
    r"approve post|phê duyệt bài viết|decline post|từ chối bài viết|"
    r"remove post|gỡ bài viết|pin post|ghim bài viết|unpin post|bỏ ghim bài viết|"
    r"feature post|unfeature post|"
    r"who can see your post\??|ai có thể xem bài viết của bạn\??|"
    r"who can see your story\??|ai có thể xem tin của bạn\??|"
    r"your story|tin của bạn|discard story|hủy tin|"
    r"create story|tạo tin|create reel|tạo reel|create post|tạo bài viết|"
    r"create room|tạo phòng|create event|tạo sự kiện|"
    r"create fundraiser|tạo gây quỹ|create group|tạo nhóm|create page|tạo trang|"
    r"search messenger|tìm kiếm messenger|new message|tin nhắn mới|new chat|cuộc trò chuyện mới|"
    r"archive chat|lưu trữ cuộc trò chuyện|delete chat|xóa cuộc trò chuyện|"
    r"ignore messages|bỏ qua tin nhắn|block messages|chặn tin nhắn|"
    r"open chat|mở cuộc trò chuyện|see chat|xem cuộc trò chuyện|"
    r"voice call|cuộc gọi thoại|video call|cuộc gọi video|"
    r"start voice call|start video call|missed call|call ended|"
    r"no internet connection|không có kết nối internet|"
    r"you'?re offline|bạn đang ngoại tuyến|waiting for network|đang chờ mạng|"
    r"back online|đã có mạng lại|"
    r"discard draft|hủy bản nháp|keep editing|tiếp tục chỉnh sửa|"
    r"post anyway|vẫn đăng|schedule post|lên lịch bài viết|"
    r"publish now|đăng ngay|save draft|lưu bản nháp|"
    r"add location|thêm vị trí|search location|tìm vị trí|remove location|xóa vị trí|"
    r"add feeling|thêm cảm xúc|add activity|thêm hoạt động|"
    r"remove feeling|remove activity|"
    r"tag photo|gắn thẻ ảnh|untag yourself|bỏ gắn thẻ khỏi bạn|remove tag|gỡ thẻ|"
    r"edit alt text|add alt text|"
    r"see translation settings|cài đặt bản dịch|"
    r"always translate|luôn dịch|never translate|không bao giờ dịch|"
    r"translate into \S+|dịch sang tiếng \S+|"
    r"comment sent|đã gửi bình luận|reply sent|đã gửi phản hồi|"
    r"tap to retry|nhấn để thử lại|"
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
