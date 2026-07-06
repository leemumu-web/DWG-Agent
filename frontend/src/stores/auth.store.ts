import { create } from 'zustand';
import type { User } from '../types/user';

interface AuthState {
  accessToken: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  setAccessToken: (token: string) => void;
  clearSession: () => void;
}

const TOKEN_KEY = 'dwg_access_token';
const USER_KEY = 'dwg_user';

/** Recover session from localStorage — survives page refreshes and new tabs.
 *  The refresh-token cookie (HTTP-only, set by backend) handles security;
 *  the access token here is a short-lived (30 min) convenience copy. */
function readSavedUser(): User | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as User;
  } catch {
    localStorage.removeItem(USER_KEY);
    return null;
  }
}

const savedToken = localStorage.getItem(TOKEN_KEY);
const savedUser = readSavedUser();

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: savedToken,
  user: savedUser,
  setSession: (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    set({ accessToken: token, user });
  },
  setAccessToken: (token) => {
    localStorage.setItem(TOKEN_KEY, token);
    set({ accessToken: token });
  },
  clearSession: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    set({ accessToken: null, user: null });
  },
}));
