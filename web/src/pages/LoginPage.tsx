import { Eye, EyeOff, LogIn, MessageSquareText } from "lucide-react";
import { useState, type SubmitEvent } from "react";
import { Navigate, useLocation } from "react-router";

import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/form";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth-context";

function redirectTarget(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from;
  return typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/proxies";
}

export function LoginPage() {
  const { username, login } = useAuth();
  const location = useLocation();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (username) {
    return <Navigate to={redirectTarget(location.state)} replace />;
  }

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(name.trim(), password);
    } catch (err) {
      setError(errorMessage(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-indigo-50 px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="flex size-12 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-lg shadow-indigo-600/20">
            <MessageSquareText className="size-6" aria-hidden />
          </span>
          <h1 className="mt-4 text-xl font-semibold text-slate-900">Đăng nhập CommentScope</h1>
          <p className="mt-1 text-sm text-slate-500">Bảng điều khiển quét comment công khai</p>
        </div>

        <form
          onSubmit={(event) => {
            void submit(event);
          }}
          className="space-y-5 rounded-2xl bg-white p-6 shadow-xl ring-1 shadow-slate-200/60 ring-slate-200"
        >
          <Field label="Tên đăng nhập" htmlFor="login-username">
            <Input
              id="login-username"
              name="username"
              autoComplete="username"
              required
              autoFocus
              value={name}
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </Field>
          <Field label="Mật khẩu" htmlFor="login-password">
            <div className="relative">
              <Input
                id="login-password"
                name="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                }}
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => {
                  setShowPassword((value) => !value);
                }}
                aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-slate-600"
              >
                {showPassword ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
              </button>
            </div>
          </Field>

          {error ? (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
              {error}
            </p>
          ) : null}

          <Button
            type="submit"
            variant="primary"
            className="w-full"
            loading={submitting}
            icon={<LogIn className="size-4" aria-hidden />}
          >
            Đăng nhập
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-slate-500">
          Tài khoản quản trị được đặt bằng <code className="font-mono">ADMIN_USERNAME</code> và{" "}
          <code className="font-mono">ADMIN_PASSWORD</code> trong file <code className="font-mono">.env</code> trên VPS.
        </p>
      </div>
    </div>
  );
}
