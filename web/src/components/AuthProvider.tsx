import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { api, clearSession, loadSession, onUnauthorized, saveSession } from "../lib/api";
import { AuthContext } from "../lib/auth-context";
import type { LoginResult } from "../lib/types";

const SESSION_EXPIRED_TOAST = "session-expired";

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState<string | null>(() => loadSession()?.username ?? null);

  useEffect(
    () =>
      onUnauthorized(() => {
        setUsername(null);
        queryClient.clear();
        toast.error("Phiên đăng nhập đã hết hạn, hãy đăng nhập lại", { id: SESSION_EXPIRED_TOAST });
      }),
    [queryClient],
  );

  useEffect(() => {
    if (loadSession()) {
      // Token bị máy chủ từ chối sẽ kích hoạt onUnauthorized ở trên; lỗi mạng thì bỏ qua.
      api.get("/api/auth/me").catch(() => undefined);
    }
  }, []);

  const login = useCallback(async (name: string, password: string) => {
    const result = await api.post<LoginResult>("/api/auth/login", { username: name, password }, { auth: false });
    setUsername(saveSession(result).username);
    toast.dismiss(SESSION_EXPIRED_TOAST);
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUsername(null);
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo(() => ({ username, login, logout }), [username, login, logout]);
  return <AuthContext value={value}>{children}</AuthContext>;
}
