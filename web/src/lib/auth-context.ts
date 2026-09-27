import { createContext, use } from "react";

export interface AuthState {
  username: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const value = use(AuthContext);
  if (!value) {
    throw new Error("useAuth phải được dùng bên trong AuthProvider");
  }
  return value;
}
