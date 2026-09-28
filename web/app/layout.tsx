import type { ReactNode } from "react";
import { SideNav } from "../components/SideNav";
import "./globals.css";

export const metadata = {
  title: "TikTok Public OSINT",
  description: "Quét hồ sơ công khai và ghi nhận kết quả đồng bộ danh bạ chính thức.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <div className="app-shell">
          <aside className="side">
            <div className="brand">
              <span>Public OSINT</span>
              <strong>TikTok Scanner</strong>
            </div>
            <SideNav />
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
