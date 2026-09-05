"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { api } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import { useEffect } from "react";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/dashboard";

  const [tenant, setTenant] = useState("acme");
  const [email, setEmail] = useState("lead@acme.vn");
  const [password, setPassword] = useState("demo1234");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated()) router.replace(next);
  }, [next, router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.login({
        tenant_code: tenant,
        email,
        password,
      });
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Đăng nhập thất bại");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_20%_20%,rgba(45,212,191,0.12),transparent_40%),radial-gradient(ellipse_at_80%_0%,rgba(56,189,248,0.08),transparent_35%),linear-gradient(160deg,#0b1220,#0f172a_45%,#020617)]" />
      <div className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:linear-gradient(rgba(148,163,184,0.4)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.4)_1px,transparent_1px)] [background-size:48px_48px]" />

      <div className="relative w-full max-w-md animate-fade-up rounded-2xl border border-slate-700/70 bg-slate-950/70 p-8 shadow-2xl backdrop-blur">
        <div className="mb-8">
          <div className="text-[11px] uppercase tracking-[0.22em] text-teal-400/90">
            AQATE Enterprise
          </div>
          <h1 className="font-display mt-2 text-3xl font-semibold tracking-tight text-slate-50">
            Telesale QA
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            Đăng nhập để xem điểm chấm, evidence, coaching và rò rỉ doanh thu.
          </p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block text-sm">
            <span className="mb-1.5 block text-slate-400">Mã tenant</span>
            <input
              value={tenant}
              onChange={(e) => setTenant(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2.5 text-slate-100 outline-none ring-teal-500/40 focus:ring-2"
              autoComplete="organization"
              required
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block text-slate-400">Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2.5 text-slate-100 outline-none ring-teal-500/40 focus:ring-2"
              autoComplete="username"
              required
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block text-slate-400">Mật khẩu</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2.5 text-slate-100 outline-none ring-teal-500/40 focus:ring-2"
              autoComplete="current-password"
              required
            />
          </label>

          {error ? (
            <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-teal-500 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-teal-400 disabled:opacity-60"
          >
            {loading ? "Đang đăng nhập…" : "Đăng nhập"}
          </button>
        </form>

        <p className="mt-5 text-xs leading-relaxed text-slate-500">
          Demo: <span className="text-slate-300">lead@acme.vn</span> /{" "}
          <span className="text-slate-300">demo1234</span>. Nếu API{" "}
          <code className="text-slate-400">NEXT_PUBLIC_API_URL</code> chưa sẵn
          sàng, console tự dùng dữ liệu demo.
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[#0b1220] text-slate-400">
          Đang tải…
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
