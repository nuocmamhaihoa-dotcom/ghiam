# Cách các AI khác học

Trọng số của một model không đổi sau mỗi câu chat. Phần “học liên tục” ở các hệ dưới đây là bộ nhớ và tài liệu, không phải huấn luyện lại mỗi lượt.

- ChatGPT giữ một bộ nhớ ngắn giữa các cuộc trò chuyện, cộng chỉ thị riêng của người dùng.
- Claude gắn tài liệu vào một project. Câu sau đọc lại các file đó.
- Gemini lưu thông tin người dùng đã chọn giữ, và dùng lịch sử cuộc trò chuyện.
- DeepSeek, Qwen, Llama, Gemma: file trọng số đứng yên. Muốn giỏi thêm thì thêm tài liệu (RAG) hoặc huấn luyện lại trên máy có GPU.
- Letta (trước là MemGPT) tách bộ nhớ lõi ngắn và kho dài. Model tự quyết định ghi gì vào kho.

Coderd học theo cùng kiểu bộ nhớ, không sửa file `.gguf`.

- Voyager: mỗi chủ đề thành một kỹ năng trong `learn/the/` (sự kiện, cách làm, ví dụ).
- Generative Agents: `learn/dong-chay.md` là dòng thời gian, `learn/suy-ngam.md` nối hai chủ đề có chung thuật ngữ.
- Reflexion: câu của model chỉ được giữ khi bám ghi chú gốc. Câu lệch thì giữ nguyên câu trong tài liệu.
- Ôn tập: mỗi vòng lấy kỹ năng ít được hỏi nhất, ghi đáp án vào `learn/on-tap.md`.
- MemGPT: `learn/kinh-nghiem.md` là bộ nhớ ngắn đưa vào câu chat. `learn/sach/` và `learn/the/` là kho dài.
- Nguồn: file nội bộ, `.coderd/memory.md`, đoạn chat đã xong, tutorial Python, thư viện chuẩn, howto, FAQ và tài liệu git.
