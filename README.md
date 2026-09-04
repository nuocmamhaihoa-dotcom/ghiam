# CallCraft — Telesale Coach

Hệ thống phân tích cuộc gọi telesale tiếng Việt: mở đầu, xử lý từ chối, chốt đơn, tốc độ nói, ngữ điệu ước lượng, từ khóa đồng ý, và kịch bản theo ngành.

## Chạy local

```bash
npm install
npm run dev
```

Mở [http://localhost:3000](http://localhost:3000).

## Tính năng MVP

- **Dashboard**: win rate, điểm opening/closing, WPM, từ khóa thắng, insight theo ngành
- **Kho cuộc gọi**: 10 cuộc demo sẵn (won/lost/callback)
- **Phân tích mới**: dán transcript `Sale:` / `Khách:` → score + coaching tips
- **Playbook ngành**: Bảo hiểm, TPCN, Điện máy, Giáo dục, BĐS

## Độ gần “người thật” (ước lượng kỹ thuật)

| Hạng mục | ~% gần coach người | Ghi chú |
|---|---|---|
| Opening / closing / từ khóa | 80–90% | Rất tốt trên transcript sạch |
| Xử lý từ chối | 75–85% | Cần đủ mẫu có nhãn kết quả |
| Tốc độ nói (WPM) | 90–98% | Đo từ text + duration |
| Ngữ điệu cảm xúc | 55–70% | Ước lượng từ text; cần audio + prosody |
| **Tổng thể coaching** | **75–85%** | Human-in-the-loop review 5–10% |

AI gọi điện thay sale (voice agent): khác bài toán — hiện ~40–65% cho hội thoại bán hàng linh hoạt.

## Kiến trúc tiếp theo (production)

1. **STT**: Whisper / Azure / PhoWhisper (tiếng Việt) + diarization Sale/Khách  
2. **Prosody**: pitch, energy, pause từ file audio  
3. **Backend realtime**: Convex (`convex/schema.ts` đã scaffold)  
4. **LLM labeling** (tùy chọn): gắn nhãn giai đoạn cuộc gọi tinh hơn heuristic  
5. **Feedback loop**: sale dùng playbook → ghi outcome → model cải thiện  

## Stack

- Next.js 15 + TypeScript + Tailwind
- Analyzer thuần TypeScript (chạy client, không cần API key)
- LocalStorage cho demo persistence

## Scripts

```bash
npm run dev      # dev server
npm run build    # production build
npm run start    # chạy bản build
```
