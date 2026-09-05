"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { clearAuth, getStoredUser, isAuthenticated } from "@/lib/auth";
import type { User } from "@/lib/types";

const NAV = [
  { href: "/dashboard", label: "Tổng quan" },
  { href: "/calls", label: "Cuộc gọi" },
  { href: "/qa", label: "QA Review" },
  { href: "/live-assistant", label: "Live Assistant" },
  { href: "/personality", label: "Personality" },
  { href: "/memory-graph", label: "Memory Graph" },
  { href: "/simulator", label: "Objection Sim" },
  { href: "/coaching", label: "Coaching" },
  { href: "/revenue", label: "Revenue Leak" },
  { href: "/fraud", label: "Fraud / Compliance" },
  { href: "/auto-sop", label: "Auto SOP" },
  { href: "/forecast", label: "Forecast" },
  { href: "/multi-product", label: "Multi-Product" },
  { href: "/portal", label: "Employee Portal" },
  { href: "/rules", label: "Rulebook" },
  { href: "/dna", label: "Conv. DNA" },
  { href: "/appeals", label: "Khiếu nại" },
  { href: "/admin", label: "Admin" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }
    setUser(getStoredUser());
    setReady(true);
  }, [pathname, router]);

  async function logout() {
    await api.logout();
    clearAuth();
    router.replace("/login");
  }

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg)] text-slate-400">
        Đang xác thực…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-slate-100">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(45,212,191,0.06),_transparent_45%),linear-gradient(180deg,#0b1220_0%,#0f172a_40%,#0b1220_100%)]" />
      <div className="relative mx-auto flex min-h-screen max-w-[1600px]">
        <aside
          className={`fixed inset-y-0 left-0 z-30 w-64 border-r border-slate-800/80 bg-slate-950/90 p-4 backdrop-blur transition lg:static lg:translate-x-0 ${
            open ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <div className="mb-8 px-2">
            <div className="text-[11px] uppercase tracking-[0.2em] text-teal-400/80">
              AQATE
            </div>
            <div className="mt-1 text-lg font-semibold tracking-tight text-slate-50">
              Telesale QA
            </div>
            <div className="mt-1 text-xs text-slate-500">Enterprise Console</div>
          </div>
          <nav className="space-y-1">
            {NAV.map((item) => {
              const active =
                pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setOpen(false)}
                  className={`block rounded-lg px-3 py-2 text-sm transition ${
                    active
                      ? "bg-teal-500/15 text-teal-200"
                      : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="absolute bottom-4 left-4 right-4 rounded-lg border border-slate-800 bg-slate-900/70 p-3">
            <div className="truncate text-sm text-slate-200">
              {user?.full_name || "User"}
            </div>
            <div className="truncate text-xs text-slate-500">{user?.email}</div>
            <button
              type="button"
              onClick={logout}
              className="mt-2 text-xs text-rose-300 hover:text-rose-200"
            >
              Đăng xuất
            </button>
          </div>
        </aside>

        {open ? (
          <button
            type="button"
            aria-label="Đóng menu"
            className="fixed inset-0 z-20 bg-black/50 lg:hidden"
            onClick={() => setOpen(false)}
          />
        ) : null}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-800/80 bg-slate-950/70 px-4 py-3 backdrop-blur lg:px-6">
            <button
              type="button"
              className="rounded-md border border-slate-700 px-2 py-1 text-sm text-slate-300 lg:hidden"
              onClick={() => setOpen(true)}
            >
              Menu
            </button>
            <div className="hidden text-sm text-slate-400 lg:block">
              AI QA Telesale Enterprise
            </div>
            <div className="text-xs text-slate-500">
              {user?.roles?.join(" · ")}
            </div>
          </header>
          <main className="flex-1 px-4 py-6 lg:px-6">{children}</main>
        </div>
      </div>
    </div>
  );
}
