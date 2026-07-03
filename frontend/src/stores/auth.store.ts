import { create } from 'zustand';
import type { User } from '../types/user';

interface AuthState {
  accessToken: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  clearSession: () => void;
}

const TOKEN_KEY = 'dwg_access_token';
const USER_KEY = 'dwg_user';

function readSavedUser(): User | null {
  const raw = sessionStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    sessionStorage.removeItem(USER_KEY);
    return null;
  }
}

const savedToken = sessionStorage.getItem(TOKEN_KEY);
const savedUser = readSavedUser();

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: savedToken,
  user: savedUser,
  setSession: (token, user) => {
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(user));
    set({ accessToken: token, user });
  },
  clearSession: () => {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
    set({ accessToken: null, user: null });
  },
}));
