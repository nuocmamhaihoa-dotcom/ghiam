import type { Metadata } from "next";
import { IBM_Plex_Sans, Source_Serif_4 } from "next/font/google";
import "./globals.css";

const plex = IBM_Plex_Sans({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-plex",
  display: "swap",
});

const serif = Source_Serif_4({
  subsets: ["latin", "vietnamese"],
  weight: ["500", "600", "700"],
  variable: "--font-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AQATE — AI QA Telesale Enterprise",
  description:
    "Enterprise console for telesale QA scoring, coaching, revenue leak and Conversation DNA.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi">
      <body className={`${plex.variable} ${serif.variable} antialiased`}>
        <style>{`
          :root {
            --font-sans: var(--font-plex), "IBM Plex Sans", sans-serif;
            --font-display: var(--font-serif), "Source Serif 4", Georgia, serif;
          }
        `}</style>
        {children}
      </body>
    </html>
  );
}
