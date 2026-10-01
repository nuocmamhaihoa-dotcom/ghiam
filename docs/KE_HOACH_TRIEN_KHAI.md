# Kế hoạch triển khai CommentScope

Tài liệu này mô tả toàn bộ hệ thống: phần đã làm xong và kế hoạch chi tiết cho các phần còn lại. Kế hoạch không ước lượng
theo ngày/tuần; độ khó của từng giai đoạn được mô tả bằng các thành phần phải thay đổi, mức độ đụng vào code hiện có,
phụ thuộc giữa các giai đoạn và rủi ro kỹ thuật.

Hướng dẫn cài đặt và vận hành phần đã có nằm trong [README](../README.md) và [agent/README.md](../agent/README.md).

## Mục lục

1. [Tóm tắt](#1-tóm-tắt)
2. [Mục tiêu, phạm vi và nguyên tắc](#2-mục-tiêu-phạm-vi-và-nguyên-tắc)
3. [Kiến trúc](#3-kiến-trúc)
4. [Hiện trạng](#4-hiện-trạng)
5. [Kho proxy (đã triển khai)](#5-kho-proxy-đã-triển-khai)
6. [Khuôn dữ liệu kết quả](#6-khuôn-dữ-liệu-kết-quả)
7. [Các giai đoạn tiếp theo](#7-các-giai-đoạn-tiếp-theo)
8. [Kiểm thử và nghiệm thu](#8-kiểm-thử-và-nghiệm-thu)
9. [Vận hành](#9-vận-hành)
10. [Bảo mật](#10-bảo-mật)
11. [Pháp lý, điều khoản nền tảng và dữ liệu cá nhân](#11-pháp-lý-điều-khoản-nền-tảng-và-dữ-liệu-cá-nhân)
12. [Rủi ro và cách giảm thiểu](#12-rủi-ro-và-cách-giảm-thiểu)
13. [Các quyết định cần chủ dự án chốt](#13-các-quyết-định-cần-chủ-dự-án-chốt)

## 1. Tóm tắt

CommentScope thu thập comment công khai của các bài viết công khai theo cơ chế:

> danh sách permalink bài viết → máy PC (worker) chạy Chromium qua proxy → trả JSON (`text`, `author`, `time`, `likes`)
> → VPS lưu, hiển thị và cho xuất file.

- **VPS là control plane**: dashboard, API, database, bộ lập lịch và kho proxy. VPS không chạy trình duyệt nên cấu hình
  nhỏ là đủ.
- **Máy PC là nơi làm việc nặng**: agent Python điều khiển Chromium bằng Playwright. Muốn tăng công suất thì thêm máy PC
  hoặc tăng số trình duyệt chạy song song trên mỗi máy.
- **Proxy tĩnh và proxy 4G xoay** được nhập hàng loạt và quản lý tập trung trên VPS. Máy PC thuê proxy theo lượt có thời
  hạn, dùng xong trả lại kèm kết quả (dùng tốt / bị chặn / lỗi) để VPS chấm điểm, cách ly proxy hỏng hoặc đổi IP proxy 4G.

**Đã xong**: kho proxy (nhập hàng loạt có xem trước, kiểm tra sống/chết, đổi IP 4G, cho máy PC thuê), dashboard quản trị
kho proxy, agent PC (thuê proxy, kiểm tra IP ra, mở Chromium qua proxy), đóng gói triển khai VPS (Docker Compose,
PostgreSQL, Caddy HTTPS). Với Facebook: tạo job từ permalink bài công khai, máy PC đọc comment công khai (nội dung, tác
giả, thời điểm, lượt thích) rồi gửi về VPS, dashboard xem và xuất JSON/CSV/NDJSON.

**Còn lại, theo thứ tự phụ thuộc**: quản lý máy PC trên dashboard → giám sát, cảnh báo, sao lưu tự động → nền tảng khác
Facebook → mở rộng khi cần.

## 2. Mục tiêu, phạm vi và nguyên tắc

### 2.1 Mục tiêu

1. Người vận hành dán danh sách permalink (từ vài bài tới hàng chục nghìn bài), chọn tuỳ chọn (số comment tối đa mỗi
   bài, có lấy trả lời hay không, pool proxy...) rồi bấm chạy.
2. Hệ thống chia việc cho các máy PC đang online. Mỗi việc dùng một proxy thuê từ kho; tiến độ hiện trên dashboard.
3. Kết quả lưu tập trung, xem và lọc trên dashboard, xuất JSON/CSV. Mỗi comment có tối thiểu `text`, `author`, `time`,
   `likes`.
4. Tận dụng tài nguyên PC: mỗi máy chạy nhiều trình duyệt song song theo cấu hình, tự nhận việc khi rảnh, tự trả việc
   khi tắt máy hoặc mất mạng.

### 2.2 Ngoài phạm vi (đề xuất)

- Nội dung không công khai: không đăng nhập tài khoản để xem nhóm kín hay bài giới hạn người xem.
- Tương tác thay người dùng: không like, comment, chia sẻ, nhắn tin.
- Vượt cơ chế chống bot bằng dịch vụ giải captcha. Gặp captcha hoặc tường đăng nhập thì coi là "bị chặn": giảm tốc,
  đổi proxy hoặc đổi IP, báo lại cho người vận hành.

### 2.3 Nguyên tắc thiết kế

- **Worker chủ động kéo việc qua HTTPS**: máy PC ở sau NAT hoặc mạng 4G vẫn chạy được, không cần IP tĩnh, không mở cổng.
  VPS chỉ mở cổng 80/443.
- **Tài nguyên dùng chung được cấp theo lượt thuê có thời hạn** (lease + TTL + gia hạn). Máy PC tắt ngang thì proxy và
  việc đang giữ tự quay về kho khi hết hạn, không cần người dọn.
- **Server là nguồn sự thật** về thời gian và trạng thái; agent chỉ báo cáo. Agent cảnh báo khi đồng hồ máy PC lệch VPS
  từ 120 giây trở lên.
- **Bí mật không rời khỏi chỗ an toàn**: mật khẩu proxy và link đổi IP được mã hoá khi lưu; token agent đọc từ biến môi
  trường hoặc file cấu hình, không qua tham số dòng lệnh (tham số hiện trong danh sách tiến trình của máy).
- **Mọi thông báo, giao diện, tài liệu bằng tiếng Việt**; thông báo lỗi nói rõ nguyên nhân và cách sửa.

## 3. Kiến trúc

### 3.1 Sơ đồ

```mermaid
flowchart LR
  Admin["Trình duyệt quản trị<br>(dashboard)"] -- HTTPS --> Caddy
  subgraph VPS["VPS: control plane"]
    Caddy["Caddy<br>HTTPS tự động"] --> App["Server FastAPI<br>API admin, API agent<br>bộ lập lịch nền"]
    App --> DB[("PostgreSQL")]
  end
  subgraph PC["Máy PC (worker), nhiều máy"]
    Agent["Agent Python"] --> Chromium["Chromium<br>(Playwright)"]
    Chromium --> Bridge["Cầu nối proxy<br>chỉ nghe 127.0.0.1"]
  end
  Agent -- "HTTPS + token agent<br>thuê proxy, nhận việc, gửi kết quả" --> Caddy
  Bridge --> Proxy["Proxy tĩnh / 4G"]
  Proxy --> Site["Trang đích<br>(bài viết công khai)"]
  App -- "kiểm tra sống/chết<br>gọi link đổi IP" --> Proxy
```

### 3.2 Thành phần

| Thành phần | Công nghệ | Vai trò |
| --- | --- | --- |
| Server (`server/`) | Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, httpx | API cho dashboard (đăng nhập admin bằng JWT), API cho agent (token riêng), bộ lập lịch nền: kiểm tra proxy, đổi IP, thu hồi lượt thuê hết hạn |
| Database | PostgreSQL 17 khi chạy thật, SQLite khi phát triển/test | Kho proxy, lượt thuê, job quét, bài viết và comment; sau này thêm máy PC |
| Dashboard (`web/`) | React 19, Vite, Tailwind CSS 4, TanStack Query | Giao diện quản trị; bản build tĩnh do chính server phục vụ |
| Reverse proxy (`deploy/Caddyfile`) | Caddy 2 | Tự xin và gia hạn chứng chỉ Let's Encrypt, HTTP/3, header bảo mật |
| Agent (`agent/`) | Python ≥ 3.11, httpx, Playwright (Chromium) | Thuê proxy, mở cầu nối proxy cục bộ, mở Chromium qua proxy, đọc comment công khai của bài Facebook |

### 3.3 Các quyết định kiến trúc

- **Cầu nối proxy trên máy PC.** Chromium không tự đăng nhập được proxy SOCKS5 có mật khẩu. Agent mở một proxy HTTP nhỏ
  trên `127.0.0.1` (mật khẩu ngẫu nhiên mỗi lần chạy) rồi chuyển tiếp qua proxy thật: HTTP, HTTPS (TLS tới chính proxy)
  hoặc SOCKS5. Cầu nối còn phân biệt lỗi do proxy với lỗi do trang đích để agent báo kết quả chính xác, và đếm lưu lượng
  (quan trọng với gói cước 4G). Tên miền đích luôn để proxy phân giải; Chromium được cấu hình không gửi WebRTC UDP ra
  ngoài proxy.
- **Server chạy đúng một tiến trình.** Hàng đợi kiểm tra/đổi IP nằm trong bộ nhớ, bộ lập lịch và bộ giới hạn đăng nhập
  sai chạy trong tiến trình server. Chạy hai bản sẽ kiểm tra và đổi IP hai lần. Khi cần nhiều tiến trình API, tách bộ
  lập lịch ra tiến trình riêng (giai đoạn 6).
- **PostgreSQL cho môi trường thật**: khoá dòng `SELECT ... FOR UPDATE SKIP LOCKED` để nhiều máy thuê proxy (và sau này
  nhận việc) cùng lúc mà không giẫm lên nhau. SQLite chỉ dùng khi phát triển; thao tác thuê được tuần tự hoá trong tiến
  trình.
- **Migration tự chạy khi khởi động** (Alembic), nên cập nhật phiên bản chỉ cần build lại và khởi động lại container.

## 4. Hiện trạng

| Hạng mục | Trạng thái |
| --- | --- |
| Kho proxy: nhập hàng loạt có xem trước, chống trùng, sửa, xoá, thao tác hàng loạt, xuất file | Xong |
| Kiểm tra sống/chết (thủ công, sau khi nhập, định kỳ) | Xong |
| Đổi IP proxy 4G: link đổi IP, `{session}`, nhà cung cấp tự xoay; theo lịch, khi bị chặn, thủ công | Xong |
| Cho máy PC thuê proxy: thuê, gia hạn, trả kèm kết quả, chấm điểm, cách ly | Xong |
| Dashboard trang "Kho proxy" | Xong |
| Agent PC: `ping`, `check`, `open`, `scrape`, `run` | Xong |
| Đóng gói VPS: Dockerfile, Docker Compose, Caddy, `.env.example` | Xong |
| Job quét comment Facebook, hàng đợi việc (dashboard: "Job quét comment") | Xong cho bài Facebook công khai |
| Bộ đọc comment Facebook trên PC (`scrape` một bài, `run` nhận việc liên tục) | Xong; không đăng nhập, không giải captcha |
| Kết quả comment: xem, lọc, xuất JSON/CSV/NDJSON (dashboard: "Kết quả comment") | Xong |
| Quản lý máy PC (dashboard: "Máy PC (worker)") | Giai đoạn 4 |
| Giám sát, cảnh báo, sao lưu tự động | Giai đoạn 5 (đã có healthcheck và hướng dẫn sao lưu thủ công) |

Đã kiểm chứng:

- Test tự động cho cả ba phần: server (SQLite và PostgreSQL, qua mạng proxy giả lập gồm proxy HTTP/SOCKS5, cổng xoay theo
  session và API đổi IP kiểu nhà cung cấp 4G), dashboard (Vitest cho phần logic), agent (proxy HTTP/HTTPS/SOCKS5 giả, VPS
  giả, Chromium thật mở trang qua cầu nối).
- Chạy thật server + mạng giả lập + agent: kiểm tra proxy tĩnh HTTP và SOCKS5, kiểm tra proxy 4G kèm đổi IP, mở Chromium
  (có và không có cửa sổ) qua cổng xoay theo session, giữ cửa sổ mở rồi đóng hoặc nhấn Ctrl+C, tự gia hạn lượt thuê
  nhiều vòng liên tiếp.
- Chạy thật toàn bộ stack Docker Compose (PostgreSQL + server + Caddy).
- Bộ đọc Facebook: HTML kiểu mbasic và JSON kiểu GraphQL (comment được lấy, bài viết không bị nhận nhầm là comment),
  phân trang "Xem thêm bình luận" không đi vào link đăng nhập, tường đăng nhập và bài không còn công khai được phân
  loại riêng. Chromium thật đọc fixture HTML cục bộ và trả đủ bốn trường nội dung, tác giả, thời điểm, lượt thích.
  Môi trường này không xác nhận được Facebook đang phục vụ comment cho IP trung tâm dữ liệu: trang yêu cầu đăng nhập
  được ghi là bị chặn rồi thử proxy khác, không có bước đăng nhập.

## 5. Kho proxy (đã triển khai)

### 5.1 Dữ liệu

**Bảng `proxies`**: mỗi dòng là một endpoint, duy nhất theo `(protocol, host, port, username)`.

- Kết nối: `kind` (`static` / `rotating`), `protocol` (`http` / `https` / `socks5`), `host`, `port`, `username`,
  `password_enc` (mã hoá Fernet), `uses_session`, `session_id`, `pool`, `note`, `enabled`, `max_concurrency` (số máy
  dùng chung tối đa).
- Đổi IP: `rotation_url_enc` (mã hoá), `rotation_method` (`GET` / `POST`), `rotation_interval_sec`,
  `rotation_cooldown_sec`, `rotate_on_block`, `rotation_state` (`idle` / `pending` / `rotating`), thời điểm và kết quả
  lần đổi gần nhất, `rotation_count`.
- Sức khoẻ: `health` (`unchecked` / `alive` / `dead`), `last_checked_at`, `last_check_error`, `latency_ms`, `exit_ip`,
  `country`, `isp`.
- Điểm và cách ly: `success_count`, `failure_count`, `consecutive_failures`, `quarantined_until`, `last_used_at`.

**Bảng `proxy_leases`**: một lượt máy PC thuê proxy. `id` (32 ký tự hex ngẫu nhiên), `proxy_id`, `worker_id`, `job_ref`,
`created_at`, `expires_at`, `released_at`, `outcome` (`ok` / `blocked` / `failed` / `cancelled` / `expired`), `detail`.
Lịch sử được giữ 7 ngày (`PROXY_LEASE_RETENTION_DAYS`).

### 5.2 Nhập hàng loạt

Mỗi dòng có dạng `<proxy> [link đổi IP] [tuỳ chọn key=value ...]`, các phần ngăn cách bằng `|`, dấu cách hoặc tab. Dòng
trống và dòng bắt đầu bằng `#` hoặc `//` được bỏ qua.

| Dạng proxy | Ví dụ |
| --- | --- |
| `host:port` | `1.2.3.4:8080` |
| `host:port:user:pass` (mật khẩu được chứa `:` và `@`) | `1.2.3.4:8080:user:pass` |
| `user:pass@host:port`, `user:pass:host:port`, `host:port@user:pass` | `user:pass@1.2.3.4:8080` |
| Có giao thức: `http://`, `https://`, `socks5://` (`socks5h://`, `socks://` được hiểu là SOCKS5) | `socks5://user:pass@1.2.3.4:1080` |
| Các cột cách nhau bằng dấu cách hoặc tab (copy từ Excel) | `1.2.3.4 8080 user pass` |
| IPv6 trong ngoặc vuông | `[2001:db8::1]:8080:user:pass` |
| Proxy 4G kèm link đổi IP (cách bằng `\|`, dấu cách, hoặc dính liền `...:pass:https://...`) | `4g.vn:10001:user:pass\|https://4g.vn/api/change-ip?key=abc` |
| Proxy 4G đổi IP bằng session | `gw.vn:7000:user-session-{session}:pass` |

- `{session}` đặt trong tên đăng nhập hoặc mật khẩu; mỗi lần đổi IP hệ thống thay bằng một mã ngẫu nhiên mới.
- Tuỳ chọn riêng từng dòng: `type` (`static`/`tinh` hoặc `4g`/`rotating`/`xoay`), `pool` (1–64 ký tự chữ, số, `.`,
  `_`, `-`), `interval` (tự đổi IP theo lịch), `cooldown` (chờ tối thiểu giữa hai lần đổi), `concurrency` (1–100 máy
  dùng chung), `method` (`GET`/`POST` khi gọi link đổi IP), `protocol` (khi dòng không ghi giao thức). Thời lượng ghi
  bằng giây hoặc `30s`, `10m`, `1h`, tối đa 24 giờ.
- Tự nhận loại: dòng có link đổi IP hoặc `{session}` là proxy 4G; các dòng còn lại theo giá trị mặc định chọn trên form.
  Số máy dùng chung mặc định: proxy tĩnh 2, proxy 4G 1.
- SOCKS4 chưa được hỗ trợ; báo lỗi rõ ràng ở từng dòng.

Quy trình trên dashboard: dán hoặc kéo-thả file `.txt` → xem trước từng dòng (mới / cập nhật / trùng / lỗi, kèm cảnh
báo) → chọn cách xử lý proxy đã có trong kho (bỏ qua hoặc cập nhật; so khớp theo giao thức, host, cổng, tên đăng nhập) →
nhập → tuỳ chọn kiểm tra sống/chết ngay. Mỗi lần nhập tối đa 20.000 dòng (`PROXY_IMPORT_MAX_LINES`); phần xem trước hiện
tối đa 2.000 dòng, ưu tiên dòng lỗi, phần tổng kết luôn tính đủ.

Xuất file theo bộ lọc đang dùng: dạng URL (`http://user:pass@host:port`) hoặc `host:port:user:pass`, tuỳ chọn kèm link
đổi IP và tuỳ chọn để nhập lại nguyên vẹn vào hệ thống khác.

### 5.3 Kiểm tra sống/chết

- Gửi request qua proxy tới lần lượt các trang trong `PROXY_CHECK_URLS` (mặc định `https://ipinfo.io/json` và
  `https://api.ipify.org?format=json`), đọc IP ra, quốc gia, nhà mạng từ JSON hoặc text.
- Lỗi kết nối tới proxy hoặc sai mật khẩu (HTTP 407) là `dead` ngay; trang kiểm tra lỗi hoặc chậm thì thử trang tiếp theo.
- Chạy song song tối đa 50 proxy (`PROXY_CHECK_CONCURRENCY`), mỗi lần chờ tối đa 15 giây (`PROXY_CHECK_TIMEOUT_SEC`).
- Tự động kiểm tra lại mỗi 900 giây (`PROXY_HEALTH_CHECK_INTERVAL_SEC`, 0 = tắt), mỗi nhịp lập lịch (5 giây) đưa tối đa
  200 proxy vào hàng đợi (`PROXY_HEALTH_CHECK_BATCH`), ưu tiên proxy lâu chưa kiểm tra.

### 5.4 Đổi IP proxy 4G

Chế độ đổi IP được suy ra từ dữ liệu của proxy:

| Chế độ | Điều kiện | Cách đổi |
| --- | --- | --- |
| `url` | Có link đổi IP | Gọi link (`GET`/`POST`, chờ tối đa 30 giây), đọc phản hồi của nhà cung cấp (`success`, `status`, `error`, `message`...). Nếu phản hồi có endpoint mới (`proxyhttp`, `proxy_socks5`, `data.proxy`...) thì cập nhật host/cổng. Chờ 8 giây rồi xác minh IP mới, tối đa 4 lần, mỗi lần cách 5 giây |
| `session` | Tên đăng nhập hoặc mật khẩu có `{session}` | Sinh session mới và kiểm tra tới khi ra IP khác (tối đa 4 lần) |
| `provider` | Không có link, không có `{session}`, nhưng có `interval` | Nhà cung cấp tự xoay; hệ thống kiểm tra theo chu kỳ và ghi nhận khi IP đổi |
| `none` | Proxy tĩnh, hoặc proxy 4G chưa có cách đổi IP | Không đổi được; dashboard gợi ý thêm link đổi IP |

- **Kích hoạt**: nút "Đổi IP" (từng proxy hoặc hàng loạt), theo lịch (`interval`), khi agent trả proxy với kết quả
  `blocked` (nếu bật "Tự đổi IP khi máy PC báo bị chặn"), hoặc khi agent xin đổi (`request_rotation`).
- **An toàn**: giữa hai lần đổi phải chờ `cooldown` (mặc định 60 giây). Proxy đang có máy dùng thì chuyển sang `pending`
  và được đổi ngay khi máy cuối cùng trả proxy, không cắt ngang máy đang chạy. Tối đa 20 lượt đổi IP song song
  (`PROXY_ROTATION_CONCURRENCY`). Server khởi động lại giữa chừng thì lượt đang dở quay về `pending` để làm lại.
- Đổi IP thành công thì xoá chuỗi lỗi liên tiếp và bỏ cách ly của proxy.

### 5.5 Cho máy PC thuê proxy

```mermaid
sequenceDiagram
  participant A as Agent trên PC
  participant V as Server trên VPS
  A->>V: Thuê proxy (worker_id, pool, loại, thời hạn)
  V-->>A: Lượt thuê + thông tin kết nối, hoặc "chưa có proxy rảnh, thử lại sau 10 giây"
  loop Trước khi hết 1/3 thời hạn
    A->>V: Gia hạn
  end
  A->>V: Trả proxy (ok / blocked / failed / cancelled, chi tiết, có xin đổi IP không)
  V-->>A: Đã trả, có xếp lịch đổi IP không, proxy bị cách ly tới khi nào
```

- **Chọn proxy**: đang bật, đang sống, không đang đổi IP, không bị cách ly, còn chỗ (số lượt thuê đang mở nhỏ hơn
  `max_concurrency`), đúng pool và loại nếu agent yêu cầu, không nằm trong danh sách loại trừ (`exclude_ids`). Ưu tiên
  proxy lâu chưa được dùng nhất để chia đều tải.
- **Nhiều máy thuê cùng lúc**: PostgreSQL khoá dòng bằng `FOR UPDATE SKIP LOCKED` và đếm lại số lượt thuê sau khi khoá
  để không vượt `max_concurrency`.
- **Thời hạn**: mặc định 600 giây, tối đa 3.600 giây. Agent tự gia hạn; lượt thuê hết hạn mà không được gia hạn sẽ bị
  server thu hồi với kết quả `expired` ở nhịp lập lịch kế tiếp.
- **Chấm điểm khi trả**:
  - `ok`: +1 lần thành công, xoá chuỗi lỗi liên tiếp.
  - `blocked`, `failed`: +1 lần thất bại. Từ lần lỗi liên tiếp thứ 3 (`PROXY_FAILURE_THRESHOLD`), proxy bị cách ly 300
    giây, sau đó gấp đôi mỗi lần lỗi tiếp theo, tối đa 3.600 giây. Proxy sắp được đổi IP thì không bị cách ly.
  - `cancelled`: agent dừng giữa chừng hoặc lỗi phía máy PC; không tính điểm cho proxy.

### 5.6 Agent dùng proxy thế nào

1. Gọi `/api/agent/ping` để xác nhận token và lấy danh sách trang kiểm tra IP (giống hệt server dùng).
2. Thuê proxy; nếu chưa có proxy rảnh thì chờ và thử lại trong thời gian `lease_wait_sec` (mặc định 120 giây).
3. Mở cầu nối cục bộ tới proxy vừa thuê, kiểm tra IP ra qua cầu nối.
4. Mở Chromium qua cầu nối, mở các trang cần mở (chụp ảnh nếu yêu cầu).
5. Chấm kết quả: lỗi do chính proxy (không kết nối được, sai mật khẩu, lỗi TLS, HTTP 502/504) là `failed`; trang đích
   trả HTTP 403 hoặc 429 là `blocked`; mở được trang là `ok`; chưa mở được trang nào là `cancelled`.
6. Luôn trả proxy kèm kết quả, kể cả khi lỗi hoặc người dùng nhấn Ctrl+C.

## 6. Khuôn dữ liệu kết quả

Mỗi bài viết cho ra một object JSON. Bốn trường bắt buộc của mỗi comment là `text`, `author`, `time`, `likes`; các trường
còn lại có thì điền, không có thì để `null`.

```json
{
  "post": {
    "url": "https://www.facebook.com/tentrang/posts/1234567890",
    "platform": "facebook",
    "scraped_at": "2026-09-27T08:15:02Z",
    "comments_reported": 1534,
    "comments_collected": 1490,
    "complete": false,
    "stop_reason": "max_comments"
  },
  "comments": [
    {
      "id": "1234567890123456",
      "parent_id": null,
      "text": "Nội dung comment",
      "author": "Nguyễn Văn A",
      "author_id": "100001234567890",
      "author_url": "https://www.facebook.com/profile.php?id=100001234567890",
      "time": "2026-09-26T15:03:00Z",
      "time_raw": "17 giờ",
      "likes": 1200,
      "likes_raw": "1,2K",
      "reply_count": 3
    }
  ]
}
```

| Trường | Ý nghĩa và cách chuẩn hoá |
| --- | --- |
| `text` | Nội dung comment, chuẩn hoá Unicode NFC, bỏ ký tự vô hình (zero-width) |
| `author` | Tên hiển thị của người viết |
| `author_id`, `author_url` | Định danh trên nền tảng nếu lấy được; có thể tắt hoặc ẩn danh hoá theo tuỳ chọn của job (mục 11) |
| `time` | Thời điểm đăng, ISO 8601 UTC. Lấy từ dữ liệu có cấu trúc của trang nếu có; nếu trang chỉ hiện thời gian tương đối ("17 giờ", "2 ngày") thì quy đổi theo thời điểm quét, giữ chuỗi gốc ở `time_raw`; không suy ra được thì `null` |
| `likes` | Số lượt thích dạng số nguyên. Quy đổi dạng rút gọn theo cả tiếng Việt và tiếng Anh ("1,2K", "3 N", "1,5 Tr", "1.5M"); chuỗi gốc giữ ở `likes_raw`; không hiển thị thì `null` |
| `id`, `parent_id` | Mã comment trên nền tảng; comment trả lời có `parent_id` là mã comment cha. Không có mã thì dùng giá trị băm của (tác giả, nội dung, thời gian gốc, comment cha) để chống trùng |
| `reply_count` | Số trả lời mà trang hiển thị |
| `post.complete`, `post.stop_reason` | Đã lấy hết hay dừng vì `max_comments`, hết thời gian cho phép, bị chặn... |

Xuất file:

- **JSON**: đúng khuôn trên, một file cho mỗi bài hoặc một mảng cho cả job.
- **NDJSON**: mỗi dòng một comment (kèm `post_url`), phù hợp dữ liệu lớn và xử lý tiếp bằng công cụ khác.
- **CSV**: các cột `post_url, id, parent_id, author, author_id, author_url, time, time_raw, likes, likes_raw,
  reply_count, text`; mã hoá UTF-8 có BOM để Excel trên Windows hiện đúng tiếng Việt; ô bắt đầu bằng `=`, `+`, `-`, `@`
  được thêm dấu `'` ở đầu để chống chèn công thức (comment là nội dung do người ngoài viết).

## 7. Các giai đoạn tiếp theo

Giai đoạn 1–3 đã được làm cho Facebook: job, hàng đợi, agent `run`/`scrape`, lưu comment và trang kết quả. Các mục dưới
đây giữ lại thiết kế đã chốt. Phần còn phải làm là quản lý máy PC, vận hành và nền tảng khác Facebook.

### 7.1 Tổng quan

Thang độ khó dùng trong tài liệu:

- **Thấp**: chủ yếu thêm mới theo khuôn mẫu đã có trong code, ít phụ thuộc bên ngoài.
- **Trung bình**: thêm bảng, migration, API và giao diện; có phần đồng thời hoặc giao dịch cần test kỹ.
- **Cao**: phụ thuộc hệ thống bên thứ ba thay đổi không báo trước, hoặc đụng vào giả định nền tảng của code hiện có.

| Giai đoạn | Thành phần thay đổi | Mức đụng vào code hiện có | Phụ thuộc | Độ khó | Rủi ro chính |
| --- | --- | --- | --- | --- | --- |
| 1. Job quét và hàng đợi việc | Server: bảng mới, module `app/jobs`, API admin và API agent. Dashboard: trang "Job quét comment" | Thấp: tách phần thuê/trả proxy trong `leasing.py` để chạy chung giao dịch với việc nhận việc | Kho proxy (đã xong) | Trung bình | Tranh chấp khi nhiều máy nhận việc cùng lúc; trạng thái lệch khi server khởi động lại |
| 2. Worker trên PC và bộ trích xuất | Agent: lệnh `run`, quản lý Chromium nhiều context, bộ trích xuất theo nền tảng, bộ đệm kết quả cục bộ | Trung bình: tái dùng `bridge`, `lease`, `verdict`, `browser`; mở rộng `ProxiedBrowser` sang nhiều context | Giai đoạn 1 | Cao | Trang đích đổi cấu trúc hoặc siết chống bot; PC hết RAM; hết dung lượng 4G |
| 3. Kết quả comment | Server: bảng `comments`, ghi theo lô chống trùng, API lọc, xuất file dạng luồng. Dashboard: trang "Kết quả comment" | Thấp | Giai đoạn 1 (schema); giai đoạn 2 cho dữ liệu thật, có thể làm song song bằng dữ liệu mẫu | Trung bình | Khối lượng dữ liệu lớn; chèn công thức CSV; dữ liệu cá nhân |
| 4. Quản lý máy PC | Server: bảng `workers`, `agent_tokens`, heartbeat và lệnh điều khiển. Dashboard: trang "Máy PC (worker)". Agent: xử lý lệnh | Trung bình: thay cơ chế xác thực agent (`require_agent`), vùng nhạy cảm về bảo mật | Giai đoạn 2 | Trung bình | Khoá nhầm máy đang chạy; tương thích ngược với `AGENT_TOKENS` |
| 5. Vận hành | Sao lưu tự động, chỉ số giám sát, cảnh báo, CI | Thấp | Không bắt buộc; chỉ số của job/máy PC cần giai đoạn 1–4 | Thấp đến trung bình | Có bản sao lưu nhưng khôi phục không được nếu chưa diễn tập |
| 6. Mở rộng | Tách tiến trình lập lịch, phân quyền nhiều quản trị viên, giới hạn tốc độ toàn cục | Cao: đụng vòng đời `ProxyRuntime` và giả định một tiến trình | Giai đoạn 1–5 | Cao | Lỗi đồng bộ giữa nhiều tiến trình |

Thứ tự đề xuất: 1 → 2 (làm trọn một nền tảng đầu tiên) → 3 → 4, phần sao lưu của giai đoạn 5 làm song song ngay từ khi
có dữ liệu thật, giai đoạn 6 chỉ làm khi số máy PC hoặc số người quản trị đòi hỏi.

### 7.2 Giai đoạn 1: Job quét comment và hàng đợi việc

**Mục tiêu**: tạo job từ danh sách permalink, chia thành từng việc (mỗi bài viết một việc), cho agent nhận việc kèm
proxy, theo dõi tiến độ, tạm dừng, tiếp tục, huỷ, chạy lại việc lỗi.

**Dữ liệu** (migration mới):

- `scrape_jobs`: `id`, `name`, `status` (`draft` / `running` / `paused` / `completed` / `cancelled`), `priority`,
  tuỳ chọn (`max_comments`, `include_replies`, `max_replies_per_comment`, `sort`, `proxy_pool`, `proxy_kind`,
  `max_attempts` mặc định 3, `time_budget_sec` cho mỗi bài, `max_parallel` cho cả job, `author_mode`), bộ đếm tiến độ,
  `created_at`, `started_at`, `finished_at`.
- `scrape_posts`: `id`, `job_id`, `url` (đã chuẩn hoá), `platform`, `status` (`pending` / `running` / `done` /
  `not_available` / `failed` / `cancelled`), `attempts`, `not_before` (thời điểm sớm nhất được thử lại),
  `last_error`, `comments_count`, `blocked_proxy_ids`, thời điểm bắt đầu/kết thúc. Duy nhất theo `(job_id, url)`; chỉ
  mục `(status, not_before)` và `(job_id, status)`.
- `scrape_attempts`: mỗi lượt xử lý một bài: `id`, `post_id`, `worker_id`, `proxy_lease_id`, `expires_at`,
  `last_seq` (số thứ tự lô kết quả gần nhất), `outcome` (`done` / `not_available` / `blocked` / `failed` /
  `cancelled` / `expired`), `detail`, chỉ số (số comment, số trang, byte qua proxy, thời gian chạy).

**Nhập permalink**: dùng lại cách làm của bộ nhập proxy: dán hàng loạt → xem trước từng dòng (hợp lệ / trùng / nền tảng
chưa hỗ trợ / lỗi) → tạo job. Chuẩn hoá URL để chống trùng: bỏ tham số theo dõi (`fbclid`, `utm_*`, `__cft__`,
`__tn__`...), gộp các biến thể tên miền (`m.`, `mbasic.`, `web.` về tên miền chính), bỏ `#...`, rút gọn đường dẫn về
dạng chuẩn của từng nền tảng.

**Hàng đợi việc**:

```mermaid
stateDiagram-v2
  [*] --> pending
  pending --> running: máy PC nhận việc
  running --> done: lấy xong comment
  running --> not_available: bài đã xoá hoặc không công khai
  running --> pending: bị chặn, lỗi, máy PC mất kết nối (còn lượt thử)
  running --> failed: hết số lần thử
  pending --> cancelled: huỷ job
  running --> cancelled: huỷ job
```

- **Nhận việc** (`POST /api/agent/work/claim`): trong một giao dịch, chọn bài `pending` đã tới `not_before`, thuộc job
  đang chạy và chưa vượt `max_parallel`; ưu tiên job có `priority` cao hơn, cùng mức thì xoay vòng giữa các job để một
  job lớn không chiếm hết máy. Khoá dòng bằng `FOR UPDATE SKIP LOCKED`, rồi thuê proxy theo pool/loại của job, loại trừ
  các proxy từng bị chặn ở chính bài này (trường `exclude_ids` đã có sẵn trong API thuê proxy). Không có proxy rảnh thì
  bài giữ nguyên `pending` và agent nhận `retry_after_sec`. `job_ref` của lượt thuê proxy ghi mã lượt xử lý để tra ngược.
- **Báo tiến độ** (`POST /api/agent/attempts/{id}/progress`): agent gửi từng lô comment kèm số thứ tự lô; server ghi
  comment (chống trùng), bỏ qua lô đã nhận (gửi lại do mạng chập chờn không sinh bản trùng), đồng thời gia hạn lượt xử lý
  và lượt thuê proxy trong một lần gọi.
- **Kết thúc** (`POST /api/agent/attempts/{id}/complete`): agent gửi kết quả cho bài (`done`, `not_available`,
  `blocked`, `failed`, `cancelled`) và kết quả cho proxy (do bộ chấm `verdict` sẵn có tính). Server cập nhật bài và bộ
  đếm của job, trả proxy qua đúng logic chấm điểm/cách ly/đổi IP ở mục 5.5.
- **Thử lại**: `blocked`, `failed`, `expired` được thử lại tới `max_attempts`, chờ tăng dần (`not_before`), dùng proxy
  khác; `not_available` không thử lại và không tính lỗi cho proxy; `cancelled` (người dùng dừng máy PC) đưa bài về hàng
  đợi và không tính vào số lần thử.
- **Thu hồi**: bộ lập lịch đánh dấu `expired` các lượt xử lý quá hạn không gia hạn (máy PC tắt ngang) và đưa bài về
  hàng đợi, giống cách thu hồi lượt thuê proxy hiện có.

**API cho dashboard**: `POST /api/jobs/preview`, `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`,
`POST /api/jobs/{id}/pause`, `resume`, `cancel`, `retry-failed`, `GET /api/jobs/{id}/posts` (lọc theo trạng thái, phân
trang).

**Dashboard**: trang "Job quét comment": danh sách job với thanh tiến độ; ngăn tạo job (dán permalink → xem trước → tuỳ
chọn → chạy); trang chi tiết job với bảng bài viết (trạng thái, số lần thử, số comment, lỗi gần nhất, máy PC, proxy).
Dữ liệu tự làm mới định kỳ khi job đang chạy (TanStack Query); chỉ chuyển sang đẩy dữ liệu thời gian thực (SSE) nếu số
người xem đồng thời đòi hỏi.

**Việc phải sửa trong code hiện có**: tách `acquire_lease` và `release_lease` trong `server/app/proxies/leasing.py`
thành hàm nhận phiên DB từ bên ngoài, để "nhận việc + thuê proxy" và "kết thúc việc + trả proxy" là một giao dịch; thêm
bước thu hồi lượt xử lý quá hạn vào `ProxyRuntime.tick()` (hoặc một runtime riêng cho job, khởi động cùng server).

**Nghiệm thu**:

- Tạo job từ 1.000 permalink dán vào (có dòng trùng, dòng lỗi), xem trước đúng, chạy/tạm dừng/tiếp tục/huỷ được.
- Test với 20 agent giả nhận việc song song trên PostgreSQL: không bài nào bị giao cho hai máy cùng lúc, không vượt
  `max_parallel` và `max_concurrency` của proxy.
- Tắt server giữa lúc đang chạy rồi bật lại: không mất việc, không trùng comment, các lượt dở dang được thu hồi và chạy lại.

### 7.3 Giai đoạn 2: Worker trên PC và bộ trích xuất comment

**Mục tiêu**: `commentscope-agent run` chạy liên tục trên PC, tự nhận việc theo sức chứa, quét comment, gửi kết quả;
nền tảng đầu tiên chạy ổn định end-to-end.

**Vòng lặp worker**:

- Cấu hình thêm `capacity` (số việc chạy song song). Mỗi context trình duyệt mở trang mạng xã hội có thể tốn vài trăm MB
  RAM, nên bắt đầu với giá trị nhỏ rồi tăng dần theo RAM/CPU thực tế của từng máy.
- Lặp: heartbeat → nhận việc khi còn chỗ trống → mỗi việc chạy trong một task asyncio riêng: cầu nối proxy riêng →
  context Chromium riêng → bộ trích xuất → gửi từng lô kết quả → kết thúc việc.
- Một tiến trình Chromium cho cả agent, mỗi việc một context với proxy riêng (Playwright hỗ trợ proxy theo context; cần
  kiểm chứng trên Windows với phiên bản Playwright đang dùng, phương án dự phòng là mỗi việc một tiến trình Chromium).
  Khởi động lại Chromium sau một số việc nhất định hoặc khi Chromium chết, để tránh rò rỉ bộ nhớ.
- Chế độ `run` mặc định không hiện cửa sổ; chặn tải ảnh, video, font qua `context.route` để tiết kiệm dung lượng 4G
  (bật/tắt theo job). Lưu lượng mỗi việc lấy từ bộ đếm của cầu nối và gửi về VPS.
- Ngữ cảnh trình duyệt khớp với proxy: `locale` và `timezone_id` theo quốc gia của IP ra (ví dụ proxy Việt Nam dùng
  `vi-VN`, `Asia/Ho_Chi_Minh`).
- Dừng êm khi nhấn Ctrl+C hoặc nhận tín hiệu dừng: ngừng nhận việc mới, chờ việc đang chạy trong một khoảng ân hạn, quá
  hạn thì kết thúc việc với `cancelled`; luôn trả proxy (giữ đúng hành vi của `LeaseSession` hiện có).
- Bộ đệm cục bộ (SQLite hoặc JSONL trong thư mục dữ liệu của agent): VPS tạm không liên lạc được thì kết quả được giữ lại
  và gửi lại sau; nhờ số thứ tự lô, gửi lại không sinh bản trùng.

**Bộ trích xuất (extractor)**: mỗi nền tảng một module cài đặt cùng một giao diện: nhận dạng URL, `extract(page, post,
options, sink)`, trả thống kê và lý do dừng. Thư viện dùng chung: cuộn trang, bấm "Xem thêm bình luận", mở trả lời, chờ
tải xong, phát hiện đã hết comment (nhiều vòng cuộn liên tiếp không có comment mới), giới hạn thời gian và số comment,
chuẩn hoá thời gian và số lượt thích (mục 6).

Cách lấy dữ liệu, theo thứ tự ưu tiên:

1. **API chính thức** nếu nền tảng có và phù hợp. Ví dụ YouTube Data API (`commentThreads.list`) trả sẵn nội dung, tác
   giả, thời điểm đăng, số lượt thích; loại này không cần trình duyệt, có thể chạy ngay trên VPS, cần API key và chịu
   hạn mức (quota) của nhà cung cấp.
2. **Đọc dữ liệu có cấu trúc mà chính trang tải về** (`page.on("response")`): mã comment, thời điểm chính xác, số lượt
   thích; ổn định hơn đọc giao diện.
3. **Đọc DOM đã hiển thị** làm phương án dự phòng: nội dung, tác giả, thời gian tương đối, số lượt thích rút gọn.

**Phân loại kết quả**:

| Tình huống | Kết quả việc | Kết quả proxy | Hệ thống làm gì |
| --- | --- | --- | --- |
| Lấy xong hoặc chạm giới hạn của job | `done` | `ok` | Ghi nhận |
| Bài bị xoá, không công khai, không tồn tại | `not_available` | `ok` | Không thử lại |
| HTTP 403/429, captcha, tường đăng nhập, trang kiểm tra bảo mật | `blocked` | `blocked` | Đổi IP proxy 4G (nếu bật), thử lại bằng proxy khác |
| Proxy lỗi (không kết nối, sai mật khẩu, 502/504) | `failed` | `failed` | Chấm điểm/cách ly proxy, thử lại bằng proxy khác |
| Giao diện lạ, bộ trích xuất lỗi | `failed` | `ok` | Thử lại; nếu lặp lại trên nhiều proxy thì cảnh báo "bộ trích xuất có thể đã hỏng" |
| Người dùng dừng máy PC | `cancelled` | `cancelled` | Đưa bài về hàng đợi, không tính điểm |

**Cài đặt trên máy PC Windows**: gói cài hoặc file nén gồm Python, agent và lệnh tải Chromium; tự chạy khi khởi động máy
(Task Scheduler hoặc chạy dưới dạng Windows service); log xoay vòng theo dung lượng. Heartbeat gửi kèm phiên bản agent;
server báo phiên bản tối thiểu để người vận hành biết máy nào cần cập nhật.

**Việc phải sửa trong code hiện có**: `ProxiedBrowser` (một context) mở rộng thành quản lý nhiều context; `LeaseSession`
dùng cho lượt thuê do server cấp kèm việc thay vì agent tự thuê; lệnh `open`/`check` giữ nguyên để chẩn đoán proxy.

**Nghiệm thu**:

- Một máy PC thật chạy `run` với `capacity` 3 trên nền tảng đầu tiên: thu được comment của các bài công khai, dữ liệu
  đúng khuôn mục 6, `time` và `likes` khớp với số hiển thị trên trang ở các mẫu kiểm tra.
- Bị chặn thì đổi proxy/đổi IP và thử lại; bài không tồn tại được đánh dấu `not_available` mà không làm hỏng điểm proxy.
- Rút dây mạng giữa chừng rồi cắm lại: kết quả đã lấy không mất, không trùng. Nhấn Ctrl+C: mọi proxy được trả, việc
  quay lại hàng đợi.

### 7.4 Giai đoạn 3: Kết quả comment

**Dữ liệu**: bảng `comments`: `id` (bigint), `job_id`, `post_id`, `platform`, `external_id`, `parent_external_id`,
`author`, `author_id`, `author_url`, `text`, `time`, `time_raw`, `likes`, `likes_raw`, `reply_count`,
`first_seen_at`, `last_seen_at`, `attempt_id`. Duy nhất theo `(post_id, external_id)`; ghi theo lô bằng upsert: quét
lại thì cập nhật số lượt thích và số trả lời, giữ `first_seen_at`. Chỉ mục `(job_id, post_id)`, `(post_id, time)`.

**API**: `GET /api/jobs/{id}/comments` (lọc theo bài, từ khoá, tác giả, khoảng thời gian, số lượt thích tối thiểu; phân
trang theo khoá để không chậm dần ở trang sau), `GET /api/jobs/{id}/export?format=json|ndjson|csv` và
`GET /api/posts/{id}/comments.json` trả dữ liệu dạng luồng (đọc DB theo từng khối, không nạp hết vào bộ nhớ).

**Dashboard**: trang "Kết quả comment": chọn job/bài, bảng comment có tìm kiếm và bộ lọc, xem theo luồng trả lời, nút
xuất file; thống kê theo job (số comment mỗi bài, bài lỗi/bị chặn).

**Tuỳ chọn tích hợp**: webhook khi job xong (POST JSON có chữ ký HMAC) hoặc token chỉ-đọc cho hệ thống khác lấy kết quả
qua API.

**Lưu trữ**: tự xoá comment cũ hơn số ngày cấu hình (bộ lập lịch), phục vụ yêu cầu về thời hạn lưu dữ liệu cá nhân.

**Nghiệm thu**: với 1 triệu comment trong DB, trang kết quả vẫn phản hồi nhanh ở mọi trang; xuất CSV/NDJSON toàn job
không làm tăng bộ nhớ server theo kích thước file; file CSV mở đúng tiếng Việt trong Excel; nội dung bắt đầu bằng `=`
không bị Excel hiểu thành công thức.

### 7.5 Giai đoạn 4: Quản lý máy PC

**Dữ liệu**:

- `workers`: `id` (worker_id), `token_id`, tên máy, hệ điều hành, phiên bản agent, `capacity`, `status` (`online` /
  `offline` / `paused` / `draining` / `disabled`), `last_seen_at`, IP công khai lần cuối, số việc đang chạy, thống kê
  (việc thành công/lỗi, số comment, lưu lượng).
- `agent_tokens`: `id`, tên (ví dụ tên máy), giá trị băm SHA-256 của token (token là chuỗi ngẫu nhiên đủ dài nên băm
  nhanh là đủ), `created_at`, `last_used_at`, `revoked_at`. Token chỉ hiện một lần lúc tạo.

**Luồng**:

- `POST /api/agent/heartbeat` định kỳ: agent gửi trạng thái (việc đang chạy, CPU/RAM, phiên bản); server trả lệnh
  (`pause`: ngừng nhận việc mới, `resume`, `drain`: làm nốt việc đang chạy rồi dừng, `stop_attempt`, sức chứa mới) và
  phiên bản agent tối thiểu.
- Máy không gửi heartbeat quá một ngưỡng thì hiện `offline`; việc của máy đó được thu hồi theo cơ chế hết hạn đã có.
- Xác thực agent: `require_agent` chấp nhận cả token trong `AGENT_TOKENS` (tương thích ngược) và token tạo từ dashboard;
  thu hồi token có hiệu lực ngay ở request kế tiếp.

**Dashboard**: trang "Máy PC (worker)": trạng thái online/offline, sức chứa, việc đang chạy, phiên bản, lần cuối liên
lạc, tốc độ, lỗi; nút tạm dừng/tiếp tục/rút máy; tạo và thu hồi token.

**Nghiệm thu**: 5 máy PC chạy cùng lúc; tạm dừng, tiếp tục, rút từng máy từ dashboard đúng như mô tả; thu hồi token của
một máy thì máy đó bị từ chối ngay, các máy khác không bị ảnh hưởng; token cũ trong `AGENT_TOKENS` vẫn chạy.

### 7.6 Giai đoạn 5: Vận hành

- **Sao lưu tự động**: container hoặc cron trên VPS chạy `pg_dump` định kỳ, giữ nhiều bản, sao ra ngoài VPS (kho lưu trữ
  tương thích S3), mã hoá bản sao lưu. `ENCRYPTION_KEY` cất riêng, không để chung với bản sao lưu DB. Diễn tập khôi phục
  lên một máy trống.
- **Giám sát**: trang tổng quan hoặc `/api/metrics` (định dạng Prometheus): proxy sống/chết/cách ly, lượt đổi IP lỗi,
  số việc chờ, việc đang chạy, máy PC online, comment mỗi phút, tỉ lệ bị chặn theo pool và nền tảng.
- **Cảnh báo** (Telegram hoặc email): có việc chờ mà không máy nào online; số proxy sống dưới ngưỡng; đổi IP lỗi hàng
  loạt; tỉ lệ lỗi của bộ trích xuất tăng đột biến; một job "mẫu" chạy định kỳ trên vài bài công khai cố định trả về 0
  comment; sao lưu thất bại; ổ đĩa sắp đầy.
- **Log**: dạng JSON có cấu trúc, xoay vòng, không bao giờ ghi bí mật (mật khẩu proxy, token, link đổi IP đầy đủ).
- **CI** (GitHub Actions): ruff, mypy, pytest cho server và agent (kèm PostgreSQL service), eslint, tsc, vitest, build
  cho dashboard, build Docker image.
- **Làm cứng VPS**: chỉ mở 22/80/443, SSH bằng khoá, tự cập nhật bản vá bảo mật; tuỳ chọn chỉ cho phép dashboard từ
  danh sách IP (Caddy `remote_ip`).

### 7.7 Giai đoạn 6: Mở rộng (khi cần)

- Tách bộ lập lịch/hàng đợi nền ra tiến trình riêng, chọn tiến trình chủ bằng PostgreSQL advisory lock; tiến trình API
  không giữ trạng thái nên chạy được nhiều bản; bộ giới hạn đăng nhập sai chuyển vào DB hoặc Redis.
- Nhiều quản trị viên với phân quyền (xem / vận hành / quản trị) và nhật ký thao tác.
- Giới hạn tốc độ toàn cục theo tên miền đích (token bucket dùng chung giữa các máy PC), bên cạnh giới hạn theo job.
- Đưa các nguồn API chính thức thành loại bộ trích xuất chạy trên VPS.
- Thêm máy PC vẫn là cách tăng công suất chính; phía VPS chủ yếu cần tối ưu DB (chỉ mục, chia bảng `comments` theo job
  hoặc theo thời gian khi dữ liệu rất lớn).

## 8. Kiểm thử và nghiệm thu

Đã có:

- **Server**: pytest với mạng giả lập (`server/tests/netsim.py`): proxy HTTP/SOCKS5 giả trả lời như trang kiểm tra IP,
  cổng xoay theo session, API đổi IP kiểu nhà cung cấp 4G (kể cả trả endpoint mới). Chạy trên SQLite và PostgreSQL
  (`TEST_POSTGRES_URL`), gồm migration và giao diện web được phục vụ.
- **Dashboard**: Vitest cho phần logic thuần (client API, bộ lọc, chọn dòng, kiểm tra dữ liệu nhập, phân trang).
- **Agent**: proxy HTTP/HTTPS/SOCKS5 giả chạy local, VPS giả đúng khuôn API, đồng hồ giả cho phần gia hạn, Chromium thật
  mở trang qua cầu nối, kịch bản Ctrl+C lúc đang khởi động Chromium.
- **Job và comment Facebook**: chuẩn hoá permalink, nhận việc song song, gửi lại lô không tạo comment trùng, xuất CSV
  chống chèn công thức, HTML/JSON mẫu, Chromium thật đọc fixture HTML cục bộ (không mở Facebook).

Còn bổ sung:

- Khởi động lại server giữa chừng khi đang có lượt xử lý dở, đo bộ nhớ khi xuất file rất lớn.
- Trang mạng xã hội giả có JavaScript (comment tải dần, HTTP 429) và vòng lặp worker end-to-end với Chromium đi qua
  proxy giả; job mẫu chạy định kỳ trên Facebook thật để phát hiện khi trang đổi cấu trúc. Môi trường build này không
  dùng để xác nhận Facebook đang trả comment cho IP trung tâm dữ liệu.
- **Giai đoạn 4**: test thu hồi token, lệnh tạm dừng/rút máy, máy mất heartbeat.
- **Hợp đồng API giữa agent và server**: dùng chung JSON Schema sinh từ OpenAPI của server để agent và server không lệch
  nhau khi thay đổi.

## 9. Vận hành

Cài đặt, cập nhật, sao lưu, khôi phục: xem [README](../README.md#triển-khai-lên-vps). Tóm tắt:

- VPS chạy `docker compose up -d --build` với ba container: `db` (PostgreSQL), `app` (server + dashboard), `caddy`
  (HTTPS). Chỉ Caddy mở cổng ra ngoài; cổng 8000 của server chỉ nằm trong mạng Docker.
- Không tăng số bản của `app` (không `--scale app=2`, không thêm `--workers`): bộ lập lịch chạy trong tiến trình server.
- Cập nhật: sao lưu → `git pull` → `docker compose up -d --build`; migration tự chạy khi server khởi động.
- Sao lưu gồm hai phần tách rời: dữ liệu (`pg_dump`) và file `.env` (chứa `ENCRYPTION_KEY`; mất khoá này là mất mật khẩu
  proxy và link đổi IP đã lưu).
- Kiểm tra sức khoẻ: `GET /api/health` (Docker dùng cho healthcheck), log bằng `docker compose logs -f app`.

## 10. Bảo mật

Đã có:

- HTTPS bắt buộc qua Caddy, bật HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  ẩn header `Server`.
- Đăng nhập dashboard: JWT ký bằng `SECRET_KEY`, hạn 720 phút; khoá tạm theo IP sau 10 lần sai trong 300 giây; so sánh
  mật khẩu và token bằng phép so sánh thời gian hằng.
- API agent: bearer token riêng (`AGENT_TOKENS`), khuyến nghị mỗi máy một token; agent cảnh báo khi gửi token qua
  `http://` tới máy không phải localhost.
- Mật khẩu proxy và link đổi IP mã hoá Fernet trong DB (`ENCRYPTION_KEY`). Danh sách proxy chỉ trả mật khẩu dạng che;
  link đổi IP bị che phần khoá API, chỉ hiện đầy đủ ở ngăn chi tiết của quản trị viên. File xuất (có mật khẩu) chỉ quản
  trị viên tải được, kèm `Cache-Control: no-store`.
- Cầu nối proxy trên PC chỉ nghe `127.0.0.1` với mật khẩu ngẫu nhiên mỗi lần chạy; tên miền đích do proxy phân giải;
  WebRTC không đi ngoài proxy.
- Container server chạy bằng người dùng không phải root; `.env` và file cơ sở dữ liệu nằm trong `.gitignore`.

Cần làm thêm:

- Token agent theo máy, lưu dạng băm, thu hồi từ dashboard (giai đoạn 4).
- Chống SSRF cho link đổi IP khi có nhiều người quản trị: chặn địa chỉ nội bộ, loopback và địa chỉ metadata của nhà cung
  cấp cloud. Hiện chỉ quản trị viên nhập được link nên rủi ro thấp.
- Chống chèn công thức khi xuất CSV (giai đoạn 3).
- Mã hoá bản sao lưu; cất `ENCRYPTION_KEY` tách khỏi bản sao lưu DB (giai đoạn 5).
- Phân quyền và nhật ký thao tác quản trị (giai đoạn 6).

## 11. Pháp lý, điều khoản nền tảng và dữ liệu cá nhân

Phần này là lưu ý kỹ thuật và rủi ro, không thay thế tư vấn pháp lý. Nên hỏi ý kiến luật sư trước khi chạy ở quy mô lớn
hoặc dùng dữ liệu cho mục đích thương mại.

- **Điều khoản của nền tảng**: nhiều nền tảng mạng xã hội (ví dụ Meta) cấm thu thập dữ liệu tự động khi chưa được cho
  phép bằng văn bản. Hậu quả có thể là chặn IP, yêu cầu gỡ dữ liệu hoặc khởi kiện. Ưu tiên API chính thức khi có; đặt
  giới hạn tốc độ để không gây tải bất thường cho trang đích.
- **Dữ liệu cá nhân**: tên, mã định danh, đường dẫn trang cá nhân và nội dung comment của người dùng là dữ liệu cá nhân
  theo pháp luật Việt Nam (Nghị định 13/2023/NĐ-CP và Luật Bảo vệ dữ liệu cá nhân có hiệu lực từ 01/01/2026). Hệ thống hỗ
  trợ tuân thủ bằng:
  - Mục đích thu thập ghi rõ ở từng job.
  - Tối thiểu hoá: tuỳ chọn `author_mode` cho mỗi job: đầy đủ (tên + mã + đường dẫn), chỉ tên, hoặc ẩn danh hoá (thay mã
    tác giả bằng HMAC với khoá bí mật của hệ thống).
  - Thời hạn lưu: tự xoá kết quả cũ theo cấu hình.
  - Bảo mật: chỉ quản trị viên truy cập, HTTPS, sao lưu mã hoá, nhật ký thao tác (giai đoạn 6).
  - Xoá theo yêu cầu: tìm và xoá comment theo tác giả.
- **Chỉ nội dung công khai**: không đăng nhập tài khoản, không vượt tường đăng nhập, không giải captcha.

## 12. Rủi ro và cách giảm thiểu

| Rủi ro | Ảnh hưởng | Giảm thiểu |
| --- | --- | --- |
| Trang đích đổi giao diện hoặc cấu trúc dữ liệu | Bộ trích xuất trả thiếu hoặc 0 comment | Ưu tiên dữ liệu có cấu trúc hoặc API chính thức; bộ mẫu test cho từng nền tảng; job "mẫu" chạy định kỳ và cảnh báo khi kết quả bất thường; bộ trích xuất có số phiên bản |
| Bị chặn IP, captcha, tường đăng nhập | Việc chậm hoặc thất bại | Proxy 4G đổi IP khi bị chặn; chấm điểm và cách ly proxy; giới hạn tốc độ theo job và theo tên miền; ngữ cảnh trình duyệt khớp quốc gia của proxy |
| API đổi IP của nhà cung cấp 4G mỗi nơi một kiểu | Đổi IP thất bại | Bộ đọc phản hồi đã nhận các dạng JSON phổ biến; xác minh bằng IP thật sau khi đổi; báo lỗi rõ trên dashboard; bổ sung dạng mới khi gặp |
| Máy PC tắt ngang, mất mạng, hết RAM | Việc treo, proxy bị giữ | Lượt thuê/lượt xử lý có hạn và tự thu hồi; `capacity` theo từng máy; khởi động lại Chromium định kỳ; bộ đệm kết quả cục bộ |
| Hết dung lượng gói cước 4G | Proxy ngừng chạy | Chặn tải ảnh/video; đếm lưu lượng mỗi việc và mỗi proxy; cảnh báo theo ngưỡng |
| Dữ liệu comment rất lớn | Chậm truy vấn, đầy ổ đĩa | Chỉ mục, phân trang theo khoá, xuất dạng luồng, thời hạn lưu, chia bảng khi cần |
| Server chỉ chạy một tiến trình | Giới hạn mở rộng phía VPS | Đủ cho quy mô hiện tại vì việc nặng nằm ở PC; khi cần thì tách tiến trình lập lịch (giai đoạn 6) |
| Lệch giờ giữa PC và VPS | Gia hạn sai, thời gian comment sai | Server là nguồn thời gian; agent gia hạn sớm (trước khi hết 1/3 thời hạn) và cảnh báo khi lệch từ 120 giây |
| Mất `ENCRYPTION_KEY` | Mất mật khẩu proxy và link đổi IP đã lưu | Sao lưu `.env` riêng, cất ở nơi an toàn; hướng dẫn trong `.env.example` và README |
| Rủi ro pháp lý và điều khoản nền tảng | Bị chặn, khiếu nại, kiện | Mục 11: API chính thức khi có, chỉ dữ liệu công khai, tối thiểu hoá, thời hạn lưu, tư vấn pháp lý |

## 13. Các quyết định cần chủ dự án chốt

1. **Nền tảng làm đầu tiên** (Facebook, YouTube, TikTok hay nền tảng khác) và loại permalink (bài của trang, bài trong
   nhóm công khai, video, reels...). Quyết định này định hình bộ trích xuất của giai đoạn 2.
2. **Dùng API chính thức khi có** (ví dụ YouTube Data API) hay chỉ dùng trình duyệt.
3. **Có lấy trả lời (reply) không**, số comment tối đa mỗi bài, thứ tự lấy (mới nhất hay phù hợp nhất nếu nền tảng hỗ trợ).
4. **Mức lưu thông tin tác giả** (đầy đủ / chỉ tên / ẩn danh hoá) và **thời hạn lưu kết quả**.
5. **Chính sách khi gặp captcha hoặc tường đăng nhập** (đề xuất: dừng bài đó, đổi IP, thử lại có giới hạn, không giải
   captcha).
6. **Quy mô phần cứng**: số máy PC và cấu hình từng máy, số proxy tĩnh, số modem hoặc cổng proxy 4G và gói cước (dung
   lượng) của từng cổng.
7. **Định dạng xuất và tích hợp**: JSON, NDJSON, CSV/Excel; có cần webhook hoặc API cho hệ thống khác không.
