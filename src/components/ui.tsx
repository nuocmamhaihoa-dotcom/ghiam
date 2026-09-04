"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

const LINKS = [
  { href: "/", label: "Tổng quan" },
  { href: "/calls", label: "Cuộc gọi" },
  { href: "/analyze", label: "Phân tích mới" },
  { href: "/playbooks", label: "Kịch bản ngành" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-[var(--line)] bg-[var(--panel)]/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-4">
          <Link href="/">
            <div className="font-[family-name:var(--font-display)] text-xl text-[var(--ink)]">
              CallCraft
            </div>
            <div className="text-xs text-[var(--muted)]">Telesale intelligence</div>
          </Link>
          <nav className="flex flex-wrap gap-1">
            {LINKS.map((l) => {
              const active = l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  className={clsx(
                    "rounded-md px-3 py-1.5 text-sm transition-colors",
                    active
                      ? "bg-[var(--accent)] text-white"
                      : "text-[var(--muted)] hover:bg-[var(--chip)] hover:text-[var(--ink)]",
                  )}
                >
                  {l.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}

export function ScoreRing({ score, label }: { score: number; label: string }) {
  const color = score >= 75 ? "var(--good)" : score >= 55 ? "var(--warn)" : "var(--bad)";
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="grid h-24 w-24 place-items-center rounded-full"
        style={{ background: `conic-gradient(${color} ${score}%, var(--chip) 0)` }}
      >
        <div className="grid h-[4.5rem] w-[4.5rem] place-items-center rounded-full bg-[var(--panel)]">
          <span className="font-[family-name:var(--font-display)] text-2xl">{score}</span>
        </div>
      </div>
      <span className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">{label}</span>
    </div>
  );
}

export function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
      <div className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">{label}</div>
      <div className="mt-2 font-[family-name:var(--font-display)] text-3xl">{value}</div>
      {hint ? <div className="mt-1 text-sm text-[var(--muted)]">{hint}</div> : null}
    </div>
  );
}

export function SectionTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-4">
      <h2 className="font-[family-name:var(--font-display)] text-2xl">{title}</h2>
      {subtitle ? <p className="mt-1 text-sm text-[var(--muted)]">{subtitle}</p> : null}
    </div>
  );
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const map: Record<string, string> = {
    won: "bg-emerald-100 text-emerald-800",
    lost: "bg-rose-100 text-rose-800",
    callback: "bg-amber-100 text-amber-900",
    unknown: "bg-slate-100 text-slate-700",
  };
  const label: Record<string, string> = {
    won: "Chốt được",
    lost: "Mất đơn",
    callback: "Gọi lại",
    unknown: "Chưa rõ",
  };
  return (
    <span className={clsx("rounded-full px-2.5 py-1 text-xs font-medium", map[outcome] ?? map.unknown)}>
      {label[outcome] ?? outcome}
    </span>
  );
}
