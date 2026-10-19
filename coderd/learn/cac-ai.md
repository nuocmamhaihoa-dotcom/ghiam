# Cách các AI khác học

Trọng số của một model không đổi sau mỗi câu chat. Phần “học liên tục” ở các hệ dưới đây là bộ nhớ và tài liệu, không phải huấn luyện lại mỗi lượt.

- ChatGPT giữ một bộ nhớ ngắn giữa các cuộc trò chuyện, cộng chỉ thị riêng của người dùng.
- Claude gắn tài liệu vào một project. Câu sau đọc lại các file đó.
- Gemini lưu thông tin người dùng đã chọn giữ, và dùng lịch sử cuộc trò chuyện.
- DeepSeek, Qwen, Llama, Gemma: file trọng số đứng yên. Muốn giỏi thêm thì thêm tài liệu (RAG) hoặc huấn luyện lại trên máy có GPU.
- Letta (trước là MemGPT) tách bộ nhớ lõi ngắn và kho dài. Model tự quyết định ghi gì vào kho.

Coderd làm bản nhỏ của cách đó. Vài phút một lần, tiến trình `coderd-learn` đọc các câu đã xong, rút tối đa ba dòng, ghi vào `learn/kinh-nghiem.md`. Lượt chat sau được đưa các dòng mới nhất. File `.gguf` không bị sửa.
