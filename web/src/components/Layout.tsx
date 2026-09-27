import clsx from "clsx";
import { ClipboardList, Globe, LogOut, MessageSquareText, MonitorSmartphone, ScanSearch } from "lucide-react";
import { NavLink, Outlet } from "react-router";

import { useAuth } from "../lib/auth-context";

const NAV_ITEMS = [{ to: "/proxies", label: "Kho proxy", icon: Globe }] as const;

const UPCOMING_ITEMS = [
  { label: "Job quét comment", icon: ScanSearch },
  { label: "Máy PC (worker)", icon: MonitorSmartphone },
  { label: "Kết quả comment", icon: ClipboardList },
] as const;

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex size-8 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-sm">
        <MessageSquareText className="size-4.5" aria-hidden />
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold text-slate-900">CommentScope</span>
        <span className="block text-xs text-slate-500">Bảng điều khiển</span>
      </span>
    </div>
  );
}

export function Layout() {
  const { username, logout } = useAuth();
  const initial = (username ?? "?").charAt(0).toUpperCase();

  return (
    <div className="min-h-screen lg:pl-64">
      <aside className="fixed inset-y-0 left-0 hidden w-64 flex-col border-r border-slate-200 bg-white lg:flex">
        <div className="px-5 py-5">
          <Brand />
        </div>
        <nav className="flex-1 space-y-6 px-3" aria-label="Chức năng">
          <div className="space-y-1">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  clsx(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    isActive ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                  )
                }
              >
                <item.icon className="size-4.5" aria-hidden />
                {item.label}
              </NavLink>
            ))}
          </div>
          <div>
            <p className="px-3 pb-1 text-xs font-medium tracking-wide text-slate-400 uppercase">Sắp có</p>
            <ul className="space-y-1">
              {UPCOMING_ITEMS.map((item) => (
                <li
                  key={item.label}
                  className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-400"
                  title="Đang phát triển, xem kế hoạch trong docs/KE_HOACH_TRIEN_KHAI.md"
                >
                  <item.icon className="size-4.5" aria-hidden />
                  <span className="flex-1">{item.label}</span>
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
                    Sắp có
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </nav>
        <div className="flex items-center gap-3 border-t border-slate-200 px-4 py-4">
          <span className="flex size-8 items-center justify-center rounded-full bg-slate-900 text-sm font-semibold text-white">
            {initial}
          </span>
          <span className="min-w-0 flex-1 leading-tight">
            <span className="block truncate text-sm font-medium text-slate-900">{username}</span>
            <span className="block text-xs text-slate-500">Quản trị viên</span>
          </span>
          <button
            type="button"
            onClick={logout}
            title="Đăng xuất"
            aria-label="Đăng xuất"
            className="rounded-md p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <LogOut className="size-4.5" aria-hidden />
          </button>
        </div>
      </aside>

      <header className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden">
        <Brand />
        <button
          type="button"
          onClick={logout}
          className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
        >
          <LogOut className="size-4" aria-hidden />
          Đăng xuất
        </button>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <Outlet />
      </main>
    </div>
  );
}
