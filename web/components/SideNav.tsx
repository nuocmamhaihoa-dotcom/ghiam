"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/", "Tổng quan"],
  ["/jobs", "Phiên quét"],
  ["/profiles", "Hồ sơ"],
  ["/sync", "Danh bạ"],
];

export function SideNav() {
  const pathname = usePathname();
  return (
    <nav>
      {LINKS.map(([href, label]) => (
        <Link key={href} href={href} className={pathname === href ? "active" : undefined}>
          {label}
        </Link>
      ))}
    </nav>
  );
}
