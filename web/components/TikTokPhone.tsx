"use client";

import { useState } from "react";

const SCREENS = [
  {
    title: "Thêm bạn bè",
    body: "Trong TikTok, mở Thêm/Tìm bạn bè rồi chọn Danh bạ.",
  },
  {
    title: "Cấp quyền",
    body: "Khi TikTok hỏi, tự quyết định có cho phép đọc danh bạ hay không.",
  },
  {
    title: "Xem gợi ý",
    body: "Ghi lại @username mà TikTok hiện ra, rồi gắn với liên hệ ở bên dưới.",
  },
];

type TikTokPhoneProps = {
  onOpen: () => void;
};

export function TikTokPhone({ onOpen }: TikTokPhoneProps) {
  const [step, setStep] = useState(0);
  const screen = SCREENS[step] ?? SCREENS[0];

  return (
    <div className="phone-device">
      <div className="phone-bezel" aria-label="Khung điện thoại hướng dẫn TikTok">
        <div className="phone-notch" />
        <div className="phone-screen">
          <p className="phone-kicker">Bước {step + 1}/3 trên điện thoại</p>
          <h3>{screen.title}</h3>
          <p>{screen.body}</p>
          <button
            type="button"
            className="secondary"
            onClick={() => setStep((current) => (current + 1) % SCREENS.length)}
          >
            {step === SCREENS.length - 1 ? "Xem lại từ đầu" : "Bước tiếp"}
          </button>
        </div>
      </div>
      <button type="button" onClick={onOpen}>Mở TikTok</button>
      <p className="hint">Cửa sổ mở trang TikTok chính thức. Danh bạ không được gửi đi.</p>
    </div>
  );
}
