import type { Metadata } from "next";
import { Be_Vietnam_Pro, Fraunces } from "next/font/google";
import { AppShell } from "@/components/ui";
import "./globals.css";

const sans = Be_Vietnam_Pro({
  subsets: ["vietnamese", "latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
});

const display = Fraunces({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-display",
});

export const metadata: Metadata = {
  title: "CallCraft — Telesale Coach",
  description:
    "Phân tích cuộc gọi telesale: opening, xử lý từ chối, chốt đơn, tốc độ nói, từ khóa và kịch bản theo ngành.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body className={`${sans.variable} ${display.variable} antialiased`}>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
