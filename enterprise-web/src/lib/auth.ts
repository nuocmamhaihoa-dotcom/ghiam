import type { AuthTokens, User } from "./types";

const TOKEN_KEY = "aqate_access_token";
const REFRESH_KEY = "aqate_refresh_token";
const USER_KEY = "aqate_user";

function canUseDom(): boolean {
  return typeof window !== "undefined";
}

export function getAccessToken(): string | null {
  if (!canUseDom()) return null;
  return (
    localStorage.getItem(TOKEN_KEY) ||
    getCookie(TOKEN_KEY) ||
    null
  );
}

export function getRefreshToken(): string | null {
  if (!canUseDom()) return null;
  return localStorage.getItem(REFRESH_KEY) || getCookie(REFRESH_KEY) || null;
}

export function getStoredUser(): User | null {
  if (!canUseDom()) return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function persistAuth(tokens: AuthTokens): void {
  if (!canUseDom()) return;
  localStorage.setItem(TOKEN_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  localStorage.setItem(USER_KEY, JSON.stringify(tokens.user));
  setCookie(TOKEN_KEY, tokens.access_token, tokens.expires_in);
  setCookie(REFRESH_KEY, tokens.refresh_token, 60 * 60 * 24 * 14);
}

export function clearAuth(): void {
  if (!canUseDom()) return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
  deleteCookie(TOKEN_KEY);
  deleteCookie(REFRESH_KEY);
}

export function isAuthenticated(): boolean {
  return Boolean(getAccessToken());
}

function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  const value = match?.[1];
  return value ? decodeURIComponent(value) : null;
}

function setCookie(name: string, value: string, maxAgeSec: number): void {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAgeSec}; SameSite=Lax`;
}

function deleteCookie(name: string): void {
  document.cookie = `${name}=; path=/; max-age=0; SameSite=Lax`;
}
